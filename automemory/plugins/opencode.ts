/**
 * automemory — opencode trigger adapter (Engine A)
 *
 * Claude Code has SessionStart/SessionEnd hooks. opencode 1.19 does not — this
 * plugin fills that role against the v2 plugin API (`{ id, setup(context) }`).
 *
 *   trigger       session.hook("prompt")  -> first prompt of a session spawns the
 *                                          reconciler (detached)
 *   handover-in   agent.transform         -> append the project map + fast-path
 *                                          reminder to the agent's instructions
 *
 * ── Measured constraints of opencode 1.19 (2026-10-15) ──────────────────────
 * These are properties of the runtime, verified by probe — not guesses:
 *
 * 1. The plugin module shape is `{ id, setup }`. The `Plugin` function type in
 *    @opencode-ai/plugin (returns Hooks) FAILS TO LOAD in 1.19:
 *    "Plugin must export a default definition with an id and an effect or setup
 *    function." The published types package is behind the runtime; read the
 *    real context by logging Object.keys(ctx).
 *
 * 2. `session.hook(name, cb)` accepts ANY name without erroring — a rejected or
 *    unknown name does not throw, it just never fires. So "registered without
 *    error" proves nothing; only an observed fire does.
 *
 * 3. Of 28 candidate lifecycle names probed, ONLY "prompt" fires. There is no
 *    idle/ended/deleted session hook exposed to plugins. This is not a gap we
 *    can engineer around — and it costs us nothing: ENGINE-A.md already records
 *    that the final session of a project can't be backfilled because no later
 *    trigger exists. We are exactly as blind as the Claude Code design intends.
 *
 * 4. `event.subscribe(filter)` returns an inert object from the promise API. Not
 *    usable. `aisdk.hook` never fired for any of 10 candidate names.
 *
 * 5. Plugins cannot be tested via bare `opencode run` — the CLI exits as soon as
 *    it has the response and the bus dies with it. Testing needs a long-lived
 *    server: `opencode serve --port N` then `opencode run --server URL ...`
 *    (with OPENCODE_PASSWORD set).
 *
 * ── Design decisions ────────────────────────────────────────────────────────
 *
 * - DEFAULT OFF, ask once. Consent lives in ~/.config/opencode/automemory.json
 *   keyed by directory — NOT in the project, so enabling recording leaves no
 *   trace in the repo and no marker file to gitignore. Toggle with
 *   `automemory enable|disable`.
 *
 * - SCRIBE GUARD. dispatch.py's headless scribe creates its own session and
 *   would re-trigger this hook forever. dispatch sets AUTOMEMORY_SCRIBE=1 in the
 *   child's env; we return immediately when we see it. Explicit beats heuristic.
 *
 * - FIRST PROMPT ONLY, not every prompt. `prompt` fires on every turn. Running
 *   the reconciler each time would cost money continuously. We reconcile once
 *   per session, which is also what "handover-in" means.
 *
 * - DETACHED. The reconciler makes model calls and takes seconds to minutes.
 *   Blocking a prompt on it would stall the user. Every failure path is silent:
 *   this must never disrupt a session.
 */

// Ships inside the automemory package; `automemory init` copies it to
// ~/.config/opencode/plugin/automemory.ts. It calls the INSTALLED `automemory`
// command — no engine path is baked in. (A hardcoded ~/Sites/automemory default
// lived here until 2026-10-29 and silently broke every trigger when the engine
// moved.) AUTOMEMORY_HOME is a dev override: run from a source checkout instead.
const ENGINE_HOME = process.env.AUTOMEMORY_HOME
const CONFIG_PATH = `${process.env.HOME}/.config/opencode/automemory.json`
const LOG_PATH = `${process.env.HOME}/.local/state/automemory/plugin.log`

/** Sessions we've already reconciled — the "first prompt only" memory. */
const seenSessions = new Set<string>()
const inFlight = new Set<string>()

/**
 * Surfaces a HUMAN drives. Headless `opencode run` reports app.name === "cli"
 * and `opencode serve` reports "server" — neither is a person starting work, so
 * neither should be prompted. Measured 2026-10-15: the binary uses exactly these
 * app.name values — acp, cli, desktop, ide, server, tui, web.
 *
 * This also means the engine's own scribe is excluded automatically (it runs
 * headless), a second line of defence behind AUTOMEMORY_SCRIBE.
 */
const HUMAN_SURFACES = new Set(["tui", "desktop", "ide", "web"])

type ProjectState = "on" | "off" | "asked"
type Config = { projects: Record<string, ProjectState> }

function log(msg: string) {
  try {
    require("node:fs").mkdirSync(`${process.env.HOME}/.local/state/automemory`, {
      recursive: true,
    })
    require("node:fs").appendFileSync(LOG_PATH, `${new Date().toISOString()} ${msg}\n`)
  } catch {}
}

function readConfig(): Config {
  try {
    const fs = require("node:fs")
    if (!fs.existsSync(CONFIG_PATH)) return { projects: {} }
    return { projects: JSON.parse(fs.readFileSync(CONFIG_PATH, "utf8")).projects ?? {} }
  } catch {
    return { projects: {} }
  }
}

export default {
  id: "automemory",

  async setup(ctx: any) {
    const dir: string = ctx.location.directory
    const fs = require("node:fs") as typeof import("node:fs")
    const { spawn } = require("node:child_process") as typeof import("node:child_process")

    if (process.env.AUTOMEMORY_SCRIBE) return
    const surface = String(ctx.app?.name ?? "unknown")
    const human = HUMAN_SURFACES.has(surface)
    log(`setup dir=${dir} surface=${surface} human=${human}`)

    // Consent entries are objects: { state: "on"|"off"|"asked", records_dir?: string }.
    // Older files stored a bare string. Accept both — comparing the whole entry to
    // "on" silently disabled recording for every project when the shape changed.
    const stateOf = (e: any) => (e && typeof e === "object" ? e.state ?? "" : e ?? "")
    const isOn = () => stateOf(readConfig().projects[dir]) === "on"

    // ── ask once, only to a person ──────────────────────────────────────────
    // Headless runs never prompt: nobody is there to answer, and that is how
    // /private/tmp and $HOME ended up in the consent file in the first place.
    if (!isOn()) {
      if (!human) {
        log(`skip ask (headless surface=${surface}) ${dir}`)
        return
      }
      const cfg = readConfig()
      if (cfg.projects[dir] === undefined) {
        cfg.projects[dir] = { state: "asked" }
        try {
          fs.mkdirSync(`${process.env.HOME}/.config/opencode`, { recursive: true })
          fs.writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2) + "\n")
        } catch {}
        log(`ask-once: not recording ${dir} (enable: automemory enable)`)
      }
      return
    }

    // ── handover-in: the project map + the fast-path reminder ───────────────
    await ctx.agent
      .transform(async (draft: any) => {
        const entry = readConfig().projects[dir]
        const base = (entry && typeof entry === "object" && entry.records_dir)
          ? `${entry.records_dir}/agent-records`
          : `${dir}/agent-records`
        const idx = `${base}/INDEX.md`
        const map = fs.existsSync(idx)
          ? fs.readFileSync(idx, "utf8").trim()
          : "_(no sessions recorded for this project yet)_"
        const block = [
          "",
          "<automemory>",
          "Session map for this project (derived, newest first):",
          map,
          "",
          "At a natural checkpoint, write or refresh this session's record into",
          "agent-records/: `## Done` required — a concrete durable state-change",
          "that a future session would need. If there isn't one, skip the record",
          "entirely. Optional `## Learnings & Gotchas` / `## Outstanding`.",
          "Facts about what happened, not a summary of the conversation.",
          "</automemory>",
        ].join("\n")

        for (const agent of draft.list()) {
          if (agent.hidden || agent.id === "compaction" || agent.id === "title") continue
          try {
            draft.update(agent.id, (a: any) => {
              if (typeof a.instructions === "string") a.instructions += block
              else if (typeof a.prompt === "string") a.prompt += block
              else if (typeof a.system === "string") a.system += block
            })
          } catch (e) {
            log(`handover-in update failed for ${agent.id}: ${e}`)
          }
        }
        log(`handover-in applied to agents in ${dir}`)
      })
      .catch((e: any) => log(`agent.transform failed: ${e}`))

    // ── trigger: first prompt of a session ─────────────────────────────────
    await ctx.session
      .hook("prompt", async (arg: any) => {
        try {
          const sid = arg?.sessionID
          if (!sid || seenSessions.has(sid) || inFlight.has(dir)) return
          seenSessions.add(sid)
          inFlight.add(dir)
          log(`reconcile trigger session=${sid} dir=${dir}`)

          const [cmd, args, extraEnv]: [string, string[], Record<string, string>] = ENGINE_HOME
            ? ["python3", ["-m", "automemory", "reconcile"], { PYTHONPATH: ENGINE_HOME }]
            : ["automemory", ["reconcile"], {}]
          const child = spawn(cmd, args, {
            cwd: dir,
            detached: true,
            stdio: ["pipe", "ignore", "ignore"],
            env: { ...process.env, ...extraEnv, AUTOMEMORY_SCRIBE: "1" },
          })
          // A missing command must leave a trace, not vanish (issue #5).
          child.on("error", (e: any) => {
            inFlight.delete(dir)
            log(`reconcile spawn failed (${cmd}): ${e?.message ?? e}`)
          })
          child.stdin?.end(JSON.stringify({ cwd: dir, session_id: sid }))
          child.unref()
          setTimeout(() => inFlight.delete(dir), 120_000)
        } catch (e) {
          inFlight.delete(dir)
          log(`trigger failed: ${e}`)
        }
      })
      .catch((e: any) => log(`prompt hook failed: ${e}`))

    log(`armed in ${dir}`)
  },
}