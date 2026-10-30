"""redact.py — cautious secret scrub (default-deny; dev/design/PRODUCT.md).

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
    ("huggingface-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("xai-key", re.compile(r"\bxai-[A-Za-z0-9]{20,}")),
    ("sendgrid-key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}")),
    ("bearer-token", re.compile(r"(?i)(?<=bearer )[A-Za-z0-9_\-.=+/]{16,}")),
]

# --- group patterns: only a captured group is the secret (keep structure) -----
# URL credentials: scheme://user:PASSWORD@host  -> redact the password (group 2).
_URL_CRED = re.compile(r"([a-zA-Z][\w+.\-]*://[^\s:@/]+:)([^\s:@/]+)(@)")
# Labeled assignment: password=... / api_key: "..." -> redact the value (group 2).
_LABELED = re.compile(
    r"""(?ix)
    \b(?:[A-Za-z0-9]+[_-])*               # prefixed labels: OPENAI_API_KEY, DB_PASSWORD
    (password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|
       secret[_-]?key|private[_-]?key|client[_-]?secret|auth[_-]?token|bearer)
    \b \s* [:=] \s* ["']?
    (?!\[REDACTED)                 # don't re-match an already-redacted placeholder
    ([^\s"',;]{6,})
    """)


# --- the cautious net: long random-looking strings ------------------------------
# Owner's call (2026-10-30): err cautious. Any run of 32+ token characters mixing
# upper case, lower case and digits is scrubbed — UNLESS it is one of the receipt
# shapes records need as provenance (listed in the README):
_RANDOMISH = re.compile(r"(?<![A-Za-z0-9_\-+=])[A-Za-z0-9_\-+=]{32,}(?![A-Za-z0-9_\-+=])")
_RECEIPT = [
    re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),  # UUID
    re.compile(r"(?:ses|msg|prt|toolu|req)_[A-Za-z0-9]+"),   # opencode / Claude ids
    re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}"),                # git commit / sha256
]


def _is_receipt(tok: str) -> bool:
    return any(p.fullmatch(tok) for p in _RECEIPT)


def _randomish(m, counts: Counter) -> str:
    tok = m.group(0)
    if _is_receipt(tok) or "[REDACTED" in tok:
        return tok
    if re.fullmatch(r"[0-9a-fA-F]+", tok):
        # Hex that ISN'T git/sha256 length (already let through above): many API
        # keys are exactly this shape (e.g. 32-hex), so scrub it.
        counts["high-entropy"] += 1
        return _PH.format(kind="high-entropy")
    if not (re.search(r"[a-z]", tok) and re.search(r"[A-Z]", tok) and re.search(r"[0-9]", tok)):
        return tok  # identifiers like long_snake_case_names aren't secrets
    counts["high-entropy"] += 1
    return _PH.format(kind="high-entropy")


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
    text = _RANDOMISH.sub(lambda m: _randomish(m, counts), text)

    return text, counts
