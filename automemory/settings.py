"""User preferences — one file, changed with `automemory config`, never by editing code.

`~/.config/automemory/config.json` (or `$AUTOMEMORY_SETTINGS`). Only preferences live here;
machine state the opencode plugin reads (which folders are on, how to launch the engine)
stays in `~/.config/opencode/automemory.json`. Settings shape FUTURE updates only — records
are append-only, so nothing already written is rewritten.
"""
import json
import os
from pathlib import Path

# name: (default, kind, what it does). kind: "bool" | "timezone" | "per-tool" | "choice:a,b"
SETTINGS = {
    "timezone": ("local", "timezone",
                 "time zone for update headers and record dates: local (this machine), UTC, "
                 "or a zone name like Asia/Singapore"),
    "show_offset": (True, "bool", "show the UTC offset after the time (21:25 +08)"),
    "show_stretch": (True, "bool", "number each day's updates in a record (Stretch #2)"),
    "show_scribe": (True, "bool", "name the model that wrote each update (scribe …)"),
    "git": ("push", "choice:off,commit,push",
            "commit records to each project's own `agent-records` branch, and push it — "
            "asked once per project first; this caps what any project may do"),
    "scribe_model": ({}, "per-tool",
                     "the model that writes records, per tool: claude-code, codex, opencode "
                     "(unset = the tool's cheapest; set with scribe_model.<tool>)"),
}
TOOLS = ("claude-code", "codex", "opencode")
_LEGACY = Path.home() / ".config" / "opencode" / "automemory.json"  # scribe_model lived here


def path() -> Path:
    return Path(os.environ.get("AUTOMEMORY_SETTINGS")
                or (Path.home() / ".config" / "automemory" / "config.json"))


def _read(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def stored() -> dict:
    """Only what the user set (no defaults)."""
    return {k: v for k, v in _read(path()).items() if k in SETTINGS}


def load() -> dict:
    """Every setting: the user's value, else the default."""
    out = {k: (dict(d) if isinstance(d, dict) else d) for k, (d, _, _) in SETTINGS.items()}
    legacy = _read(Path(os.environ.get("AUTOMEMORY_CONFIG") or _LEGACY)).get("scribe_model")
    if isinstance(legacy, dict):  # older installs set it there; this file wins
        out["scribe_model"].update(legacy)
    for k, v in stored().items():
        if k == "scribe_model" and isinstance(v, dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def get(name: str):
    return load()[name]


def _parse(name: str, kind: str, raw: str):
    if kind == "bool":
        v = raw.strip().lower()
        if v in ("on", "true", "yes", "1"):
            return True
        if v in ("off", "false", "no", "0"):
            return False
        raise ValueError(f"{name} is on or off, not {raw!r}")
    if kind.startswith("choice:"):
        allowed = kind.split(":", 1)[1].split(",")
        v = raw.strip().lower()
        if v not in allowed:
            raise ValueError(f"{name} is one of: {', '.join(allowed)}")
        return v
    if kind == "timezone":
        v = raw.strip()
        if v.lower() in ("local", "utc"):
            return v.lower() if v.lower() == "local" else "UTC"
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(v)
        except Exception:
            raise ValueError(f"unknown time zone {raw!r} — use local, UTC, or a name like "
                             f"Asia/Singapore")
        return v
    return raw.strip()


def _split(key: str):
    name, _, tool = key.partition(".")
    if name not in SETTINGS:
        raise KeyError(f"no setting {name!r} — `automemory config` lists them")
    kind = SETTINGS[name][1]
    if kind == "per-tool" and tool not in TOOLS:
        raise KeyError(f"use {name}.<tool>, where <tool> is one of: {', '.join(TOOLS)}")
    if kind != "per-tool" and tool:
        raise KeyError(f"{name} has no parts")
    return name, kind, tool


def _write(data: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def set_value(key: str, raw: str):
    name, kind, tool = _split(key)
    value = _parse(name, kind, raw)
    data = _read(path())
    if tool:
        data.setdefault(name, {})[tool] = value
    else:
        data[name] = value
    _write(data)
    return value


def reset(key: str) -> None:
    name, _, tool = _split(key)
    data = _read(path())
    if tool:
        (data.get(name) or {}).pop(tool, None)
        if not data.get(name):
            data.pop(name, None)
    else:
        data.pop(name, None)
    _write(data)


def show(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, dict):
        return ", ".join(f"{t}={value[t]}" for t in TOOLS if value.get(t)) or "(each tool's cheapest)"
    return str(value)
