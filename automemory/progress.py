"""progress.py — show a person that a long run is working, without bothering a hook.

A manual run can sit for minutes while the scribe writes; with no output it looks
hung (owner, 2026-10-30, #5). When stderr is a terminal, a one-line spinner with
the session name and elapsed seconds redraws in place, then gives way to a result
line. When it isn't (a hook, the opencode plugin's log), nothing is drawn — logs
get the summary lines only.
"""
from __future__ import annotations

import itertools
import sys
import threading
import time

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def interactive(stream=None) -> bool:
    stream = stream or sys.stderr
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


class Spinner:
    """with Spinner("writing 1/3 · ENGINE DEV (Opus)"): ...  — no-op off a terminal."""

    def __init__(self, label: str, stream=None):
        self.label, self.stream = label, stream or sys.stderr
        self.on = interactive(self.stream)
        self._stop = threading.Event()
        self._t = None

    def _run(self):
        start = time.monotonic()
        for frame in itertools.cycle(_FRAMES):
            if self._stop.wait(0.1):
                break
            secs = int(time.monotonic() - start)
            self.stream.write(f"\r{frame} {self.label} · {secs}s ")
            self.stream.flush()

    def __enter__(self):
        if self.on:
            self._t = threading.Thread(target=self._run, daemon=True)
            self._t.start()
        return self

    def __exit__(self, *exc):
        if self.on:
            self._stop.set()
            self._t.join()
            self.stream.write("\r\033[K")  # clear the spinner line
            self.stream.flush()
        return False


def note(line: str, stream=None) -> None:
    """A per-session result line, shown only to a person at a terminal."""
    stream = stream or sys.stderr
    if interactive(stream):
        stream.write(line + "\n")
        stream.flush()
