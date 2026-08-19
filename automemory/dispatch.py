"""dispatch.py — headless model call to author a record from a transcript.

Runs `claude -p` as a subprocess (dispatch-via-CLI, BUILD-SPEC §5 — keeps the
product zero-dependency: no SDK). Verified mechanism (2026-08-13, claude 2.1.212):
  printf '<transcript>' | claude -p '<instruction>' \
     --model <m> --output-format json --disallowed-tools <all>
  -> stdin is the transcript, -p arg is the instruction; they combine.
  -> JSON stdout has: result (text), is_error, total_cost_usd, usage, stop_reason.

Design choices:
  - Tools OFF: authoring reads text and emits text — no repo/vault access, no
    side effects (also keeps it blind to the vault, like the reference engine).
  - Neutral cwd: run in a tempdir so the call does NOT load the repo's CLAUDE.md
    or project context into the authored record.
  - Never raises for model/CLI failure — returns a DispatchResult with is_error
    so the reconciler can log-and-continue (no silent failures, BUILD-SPEC §0).
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field

# Built-in tools we explicitly deny (variadic flag must be kept LAST in argv).
_NO_TOOLS = [
    "Bash", "Edit", "MultiEdit", "Write", "Read", "NotebookEdit",
    "Task", "Agent", "WebFetch", "WebSearch", "Glob", "Grep",
]


@dataclass
class DispatchResult:
    text: str = ""
    is_error: bool = False
    error: str = ""
    cost_usd: float = 0.0
    usage: dict = field(default_factory=dict)
    model: str = ""


def author(
    transcript_text: str,
    instruction: str,
    *,
    model: str = "haiku",
    append_system: str = "",
    cli: str = "claude",
    timeout_s: int = 180,
) -> DispatchResult:
    """Dispatch one headless authoring call. Returns a DispatchResult; never
    raises for CLI/model errors (is_error carries them)."""
    cmd = [cli, "-p", instruction, "--model", model, "--output-format", "json"]
    if append_system:
        cmd += ["--append-system-prompt", append_system]
    cmd += ["--disallowed-tools", *_NO_TOOLS]  # variadic — keep last

    with tempfile.TemporaryDirectory() as neutral:
        try:
            proc = subprocess.run(
                cmd,
                input=transcript_text,
                capture_output=True,
                text=True,
                cwd=neutral,
                timeout=timeout_s,
            )
        except FileNotFoundError:
            return DispatchResult(is_error=True, error=f"{cli} not found on PATH")
        except subprocess.TimeoutExpired:
            return DispatchResult(is_error=True, error=f"dispatch timed out after {timeout_s}s")

    if proc.returncode != 0 and not proc.stdout.strip():
        return DispatchResult(
            is_error=True, error=f"exit {proc.returncode}: {proc.stderr.strip()[:200]}"
        )

    try:
        d = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return DispatchResult(is_error=True, error="could not parse claude JSON output")

    if d.get("is_error"):
        return DispatchResult(
            is_error=True,
            error=str(d.get("result") or d.get("subtype") or "model error"),
            cost_usd=float(d.get("total_cost_usd") or 0.0),
            model=model,
        )

    return DispatchResult(
        text=(d.get("result") or "").strip(),
        cost_usd=float(d.get("total_cost_usd") or 0.0),
        usage=d.get("usage") or {},
        model=model,
    )
