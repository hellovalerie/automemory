#!/usr/bin/env bash
# vault-lint.sh — mechanical vault health checks (Phase 5, issue #5).
#
# Stolen from the Obsidian second-brain post per system/design/PRIOR-ART.md's "steal"
# list. doc-philosophy rule 4: mechanize the fastest-rotting invariants. This is a
# script, not an agent: every check here is a dumb invariant that rots silently
# between sweeps.
#
# Checks:
#   1. Orphans          — entries in wins/ practitioner/ research/threads/ system/design/ not listed in INDEX.md
#   2. Index rot        — INDEX.md lines pointing at files that don't exist
#   3. Dead links       — [[wikilinks]] and relative markdown links with no target
#   4. Frontmatter      — missing/invalid frontmatter on entry files (per system/design/SCHEMA.md);
#                         field notes missing the export-UUID frontmatter key (SCHEMA.md,
#                         "Field-note updates": conversations are keyed by UUID, not title)
#   5. Held-open safety — held-open entries touched by consolidation. Per SCHEMA.md /
#                         TEXTURE-CRITERIA.md, held-open entries are exempt from
#                         consolidation and only Loh closes them. Mechanized as:
#                         (a) a held-open entry carrying superseded_by (something closed it),
#                         (b) a held-open entry modified, after its creation commit, by a
#                             commit whose message looks like automation/consolidation
#                             ("automemory:", "sweep", "consolidat").
#
# Exit 0 if clean, 1 if any findings. Human-readable output grouped by check.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT" || exit 2

FINDINGS=0

finding() {
  printf '  %s\n' "$1"
  FINDINGS=$((FINDINGS + 1))
}

header() {
  printf '\n== %s\n' "$1"
}

# Streams whose entries must appear in INDEX.md (SCHEMA.md: "one line per entry").
INDEX_STREAMS="wins practitioner research/threads system/design"
# Entry streams whose files must carry the SCHEMA.md frontmatter block.
# (practitioner/glossary/ nests a different frontmatter schema — excluded, see list_md_entries.)
ENTRY_STREAMS="wins practitioner research/threads"
# All vault content scanned for links (exports/ and seed material are raw inputs, not vault).
LINK_SCOPE="wins practitioner research/threads ops/sweeps fieldnotes system/design system/engine"

list_md() { # list_md <dir>... — md files under dirs, sorted
  find "$@" -name '*.md' -type f 2>/dev/null | sort
}

list_md_entries() { # like list_md, but excludes practitioner/glossary/ (own frontmatter schema)
  list_md "$@" | grep -v '/glossary/'
}

# ---------------------------------------------------------------- 1. orphans
header "1. Orphaned entries (not referenced in INDEX.md)"
while IFS= read -r f; do
  rel="${f#./}"
  if ! grep -qF "($rel)" INDEX.md; then
    finding "$rel — no INDEX.md line"
  fi
done < <(list_md $INDEX_STREAMS)

# -------------------------------------------------- 2. INDEX lines w/o files
header "2. INDEX.md lines pointing at missing files"
while IFS=: read -r line target; do
  [ -n "$target" ] || continue
  case "$target" in
    http://*|https://*|mailto:*|\#*) continue ;;
  esac
  target="${target%%#*}"
  if [ ! -f "$target" ]; then
    finding "INDEX.md:$line — target does not exist: $target"
  fi
done < <(grep -n -o '](\([^)]*\))' INDEX.md 2>/dev/null \
          | sed 's/^\([0-9]*\):](\(.*\))$/\1:\2/')

# ------------------------------------------------------------- 3. dead links
header "3. Dead links (wikilinks and relative markdown links)"
while IFS= read -r f; do
  rel="${f#./}"
  dir="$(dirname "$f")"

  # [[wikilinks]] — Obsidian-style: target resolves anywhere in the vault.
  while IFS=: read -r line wl; do
    [ -n "$wl" ] || continue
    inner="${wl#[[}"; inner="${inner%]]}"
    inner="${inner%%|*}"; inner="${inner%%#*}"
    [ -n "$inner" ] || continue
    if [ ! -f "$inner" ] && [ ! -f "$inner.md" ] && \
       [ -z "$(find . -path ./exports -prune -o -name "$inner.md" -type f -print 2>/dev/null | head -1)" ] && \
       [ -z "$(find . -path ./exports -prune -o -name "*-$inner.md" -type f -print 2>/dev/null | head -1)" ]; then
      # second find: Obsidian-style suffix match — [[identity-vs-self-perception]]
      # resolves to wins/2026-06-26-identity-vs-self-perception.md (date prefix)
      finding "$rel:$line — dead wikilink [[$inner]]"
    fi
  done < <(grep -n -o '\[\[[^]]*\]\]' "$f" 2>/dev/null)

  # relative markdown links
  while IFS=: read -r line target; do
    [ -n "$target" ] || continue
    case "$target" in
      http://*|https://*|mailto:*|\#*|/*) continue ;;
    esac
    target="${target%%#*}"
    [ -n "$target" ] || continue
    # Only plausible file targets (has a slash or an extension); skips doc
    # placeholders like the literal `(path)` example in SCHEMA.md.
    case "$target" in
      */*|*.*) : ;;
      *) continue ;;
    esac
    if [ ! -e "$dir/$target" ] && [ ! -e "$target" ]; then
      finding "$rel:$line — dead link: $target"
    fi
  done < <(grep -n -o '](\([^)]*\))' "$f" 2>/dev/null \
            | sed 's/^\([0-9]*\):](\(.*\))$/\1:\2/')
done < <(list_md $LINK_SCOPE; ls ./*.md 2>/dev/null)

# ------------------------------------------------------------ 4. frontmatter
header "4. Missing / invalid frontmatter"

# fm_get <file> <key> — value of a frontmatter key, empty if absent
fm_get() {
  awk -v key="$2" '
    NR==1 { if ($0 != "---") exit; next }
    /^---$/ { exit }
    $0 ~ "^"key":" { sub("^"key":[ \t]*", ""); sub("[ \t]*(#.*)?$", ""); print; exit }
  ' "$1"
}

has_fm() { [ "$(head -1 "$1")" = "---" ]; }

while IFS= read -r f; do
  rel="${f#./}"
  stream="${rel%%/*}"
  case "$rel" in
    research/threads/*) stream="threads" ;;
  esac

  if ! has_fm "$f"; then
    finding "$rel:1 — missing frontmatter block"
    continue
  fi

  # Required keys: dated entries need the full set; threads are standing files
  # (no date/source in practice, keyed by sightings instead).
  if [ "$stream" = "threads" ]; then
    required="captured type texture_criteria_version status"
  else
    required="date captured type source texture_criteria_version status"
  fi
  for key in $required; do
    if [ -z "$(fm_get "$f" "$key")" ]; then
      finding "$rel:1 — frontmatter missing key: $key"
    fi
  done

  # Drift guard: the old `criteria:` key was renamed to `texture_criteria_version:`
  # (it read like a capture date, not a pointer to TEXTURE-CRITERIA.md's version)
  # everywhere in the vault. Flag any reintroduction so it can't creep back.
  if [ -n "$(fm_get "$f" criteria)" ]; then
    finding "$rel:1 — leftover old key 'criteria:' (renamed to texture_criteria_version:)"
  fi

  type="$(fm_get "$f" type)"
  case "$type" in
    ""|win|held-open|practitioner|thread|voice|disposition) : ;;
    *) finding "$rel:1 — invalid type: $type" ;;
  esac

  status="$(fm_get "$f" status)"
  case "$status" in
    ""|open|held-open|superseded|resolved) : ;;
    *) finding "$rel:1 — invalid status: $status" ;;
  esac

  if [ "$status" = "superseded" ] && [ -z "$(fm_get "$f" superseded_by)" ]; then
    finding "$rel:1 — status: superseded but superseded_by is empty"
  fi

  for key in date captured; do
    v="$(fm_get "$f" "$key")"
    if [ -n "$v" ] && ! printf '%s' "$v" | grep -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'; then
      finding "$rel:1 — $key is not YYYY-MM-DD: $v"
    fi
  done

  # Filename convention (SCHEMA.md): dated slug for entries, none for threads.
  base="$(basename "$rel")"
  if [ "$stream" != "threads" ] && ! printf '%s' "$base" | grep -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2}-.+\.md$'; then
    finding "$rel — filename not YYYY-MM-DD-short-slug.md"
  fi
done < <(list_md_entries $ENTRY_STREAMS)

# Field notes: SCHEMA.md keys conversations by export UUID in frontmatter.
while IFS= read -r f; do
  rel="${f#./}"
  if ! has_fm "$f"; then
    finding "$rel:1 — field note has no frontmatter (SCHEMA.md: keyed by export UUID)"
  elif [ -z "$(fm_get "$f" uuid)" ]; then
    finding "$rel:1 — field note frontmatter has no uuid key"
  fi
done < <(list_md fieldnotes)

# ----------------------------------------- 5. held-open touched by consolidation
header "5. Held-open entries touched by consolidation"
CONSOLIDATION_RE='automemory:|sweep|consolidat'
while IFS= read -r f; do
  rel="${f#./}"
  [ "$(fm_get "$f" status)" = "held-open" ] || continue

  # (a) something closed it: held-open must never carry superseded_by
  sb="$(fm_get "$f" superseded_by)"
  if [ -n "$sb" ]; then
    finding "$rel:1 — held-open entry carries superseded_by: $sb"
  fi

  # (b) modified after creation by an automation/consolidation commit
  log="$(git log --format='%h %s' -- "$rel" 2>/dev/null)"
  n="$(printf '%s\n' "$log" | grep -c . || true)"
  if [ "$n" -gt 1 ]; then
    # .vault-lint-allow: hash-per-line allowlist for the user-authorized bulk
    # migrations (e.g. 76a697b, the texture_criteria_version rename sweep) —
    # authorized schema migrations are not consolidation.
    hits="$(printf '%s\n' "$log" | sed '$d' | grep -iE "$CONSOLIDATION_RE" || true)"
    if [ -n "$hits" ] && [ -f "$ROOT/.vault-lint-allow" ]; then
      hits="$(printf '%s\n' "$hits" | grep -vFf <(grep -oE '^[0-9a-f]+' "$ROOT/.vault-lint-allow") || true)"
    fi
    if [ -n "$hits" ]; then
      while IFS= read -r c; do
        finding "$rel — modified after creation by consolidation commit: $c"
      done <<EOF
$hits
EOF
    fi
  fi
done < <(list_md_entries $ENTRY_STREAMS)

# ------------------------------------------------- 6. Edits ledger presence
# SCHEMA.md: every quoting artifact ends with an ## Edits section (the ledger
# licenses surgical edits; an artifact with unledgered edits is defective).
# Added 2026-07-05 after two unledgered-bracket violations were found by hand.
header "6. Missing Edits ledger (## Edits section required in quoting artifacts)"
while IFS= read -r f; do
  rel="${f#$ROOT/}"
  grep -q '^## Edits' "$f" || finding "$rel — no ## Edits section"
done < <(list_md wins practitioner; find "$ROOT/fieldnotes" -name '*.md' 2>/dev/null)

# ---------------------------------------- 7. Unverified placement (issue #11)
# SCHEMA.md: an ## Unverified section, when present, sits before ## Edits
# (Edits stays last, always). Update-appends rely on this ordering.
header "7. Unverified section placement (## Unverified must precede ## Edits)"
while IFS= read -r f; do
  rel="${f#$ROOT/}"
  u="$(grep -n '^## Unverified' "$f" | head -1 | cut -d: -f1)"
  [ -n "$u" ] || continue
  e="$(grep -n '^## Edits' "$f" | head -1 | cut -d: -f1)"
  if [ -z "$e" ] || [ "$u" -gt "$e" ]; then
    finding "$rel — ## Unverified must appear before ## Edits"
  fi
done < <(list_md wins practitioner research/threads; find "$ROOT/fieldnotes" -name '*.md' 2>/dev/null)

# --------------------------------------------------------------------- summary
printf '\n'
if [ "$FINDINGS" -eq 0 ]; then
  printf 'vault-lint: clean\n'
  exit 0
else
  printf 'vault-lint: %d finding(s)\n' "$FINDINGS"
  exit 1
fi
