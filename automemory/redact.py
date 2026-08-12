"""redact.py — heuristic secret scrub (BUILD-SPEC §7, default-deny).

Runs on the backfill read: transcript -> [redact] -> author, so credentials
never reach the authoring model or the stored record.

HONEST LIMIT (§7): best-effort pattern matching over known secret formats +
labeled assignments. NOT a guarantee — a novel or unformatted secret can slip.
High precision is chosen over high recall on purpose: over-redaction would
corrupt the transcript (session UUIDs and git SHAs are load-bearing provenance,
base64 data is common) and break the authoring pass. So we do NOT blanket-redact
"high-entropy strings"; we match shapes we're confident about.

Matches become [REDACTED:<kind>] so the reader knows something was there
(transparency, not silent deletion). redact() also returns a by-kind count for a
no-surprises log line — never the secret values themselves.
"""
from __future__ import annotations

import re
from collections import Counter

_PH = "[REDACTED:{kind}]"

# --- whole-match patterns: the entire match is the secret ---------------------
# Order matters: more specific prefixes (sk-ant) before generic (sk-).
_WHOLE = [
    ("private-key", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("google-oauth", re.compile(r"\bya29\.[0-9A-Za-z_\-]{20,}")),
    ("stripe-key", re.compile(r"\b[sr]k_(?:live|test)_[0-9A-Za-z]{16,}")),
    ("gitlab-token", re.compile(r"\bglpat-[0-9A-Za-z_\-]{20,}")),
    ("npm-token", re.compile(r"\bnpm_[0-9A-Za-z]{36}")),
]

# --- group patterns: only a captured group is the secret (keep structure) -----
# URL credentials: scheme://user:PASSWORD@host  -> redact the password (group 2).
_URL_CRED = re.compile(r"([a-zA-Z][\w+.\-]*://[^\s:@/]+:)([^\s:@/]+)(@)")
# Labeled assignment: password=... / api_key: "..." -> redact the value (group 2).
_LABELED = re.compile(
    r"""(?ix)
    \b(password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|
       secret[_-]?key|private[_-]?key|client[_-]?secret|auth[_-]?token|bearer)
    \b \s* [:=] \s* ["']?
    (?!\[REDACTED)                 # don't re-match an already-redacted placeholder
    ([^\s"',;]{6,})
    """)


def _sub_group(match, group: int, kind: str, counts: Counter) -> str:
    """Replace only `group`'s span within the match; keep surrounding structure."""
    counts[kind] += 1
    full = match.group(0)
    s = match.start(group) - match.start(0)
    e = match.end(group) - match.start(0)
    return full[:s] + _PH.format(kind=kind) + full[e:]


def redact(text: str):
    """Return (scrubbed_text, Counter of kind -> count). Never raises on content."""
    if not text:
        return text, Counter()
    counts: Counter = Counter()

    for kind, pat in _WHOLE:
        text = pat.sub(lambda m, k=kind: (counts.update([k]) or _PH.format(kind=k)), text)

    text = _URL_CRED.sub(lambda m: _sub_group(m, 2, "url-credential", counts), text)
    text = _LABELED.sub(lambda m: _sub_group(m, 2, "labeled-secret", counts), text)

    return text, counts
