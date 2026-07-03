#!/usr/bin/env bash
# glossary-compile.sh — compile practitioner/glossary/*.md -> practitioner/glossary/glossary.json
#
# Source of truth is the markdown (SCHEMA.md, glossary stream); the JSON is
# derived — never hand-edited — and exists for the Artifact renderer. Schema
# follows the 08-technical-glossary design: learning-first entries whose
# anchors are the metaphor and the in-practice moment; optional fields are
# omitted rather than emitted as null; fields that can hold multiple values
# (in_practice, sources) are arrays from day one.
#
# Entry fields: term, type, metaphor, plain, in_practice (array of
# paragraphs), sources (array of {uuid, label?}). Output is deterministic:
# entries sorted by term.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
GLOSS_DIR="$REPO_ROOT/practitioner/glossary"
OUT="$GLOSS_DIR/glossary.json"

# fm_get <file> <key> — value of a frontmatter key, empty if absent
fm_get() {
  awk -v key="$2" '
    NR==1 { if ($0 != "---") exit; next }
    /^---$/ { exit }
    $0 ~ "^"key":" { sub("^"key":[ \t]*", ""); sub("[ \t]*$", ""); print; exit }
  ' "$1"
}

# fm_sources_json <file> — the frontmatter `sources:` list as a JSON array of
# {uuid, label?} objects. A list item is `- <uuid>` optionally followed by
# ` # <human label>` (extractor.md, "Sources readable by a human").
fm_sources_json() {
  awk '
    NR==1 { if ($0 != "---") exit; next }
    /^---$/ { exit }
    /^sources:/ { ins=1; next }
    ins && /^[ \t]+- / { sub(/^[ \t]+- /, ""); print; next }
    ins && !/^[ \t]/ { ins=0 }
  ' "$1" | jq -R -s '
    split("\n") | map(select(length > 0)) |
    map(
      (split(" # ")) as $p |
      if ($p | length) > 1
      then { uuid: ($p[0] | sub("\\s+$"; "")),
             label: ($p[1:] | join(" # ")) }
      else { uuid: (. | sub("\\s+$"; "")) }
      end
    )'
}

# section <file> <label> — the body section opened by `**<label>**`, up to the
# next `**...**` section or a `## ` heading. The bold label is stripped; a
# provenance parenthetical after it (e.g. "(verbatim, Claude Code)") is kept
# inline, per the provenance-stays-visible principle.
section() {
  awk -v want="$2" '
    NR==1 && $0=="---" { infm=1; next }
    infm && /^---$/    { infm=0; body=1; next }
    !body { next }
    /^## / { collecting=0; next }
    /^\*\*/ {
      collecting=0
      lbl=$0; sub(/^\*\*/, "", lbl); sub(/\*\*.*/, "", lbl)
      if (lbl == want) {
        collecting=1
        line=$0
        sub(/^\*\*[^*]+\*\*[ \t]*/, "", line)
        if (line ~ /^\(/) {
          match(line, /^\([^)]*\)/)
          paren=substr(line, 1, RLENGTH); rest=substr(line, RLENGTH+1)
          sub(/^[ \t]*:[ \t]*/, "", rest)
          line=paren" "rest
        } else {
          sub(/^:[ \t]*/, "", line)
        }
        print line
      }
      next
    }
    collecting { print }
  ' "$1"
}

TMP="$(mktemp -t glossary-compile.XXXXXX)"
trap 'rm -f "$TMP"' EXIT

shopt -s nullglob
for f in "$GLOSS_DIR"/*.md; do
  term="$(fm_get "$f" term)"
  if [ -z "$term" ]; then
    printf 'WARN: %s has no term: frontmatter — skipped\n' "$f" >&2
    continue
  fi
  jq -n \
    --arg term "$term" \
    --arg type "$(fm_get "$f" type)" \
    --arg metaphor "$(section "$f" Metaphor)" \
    --arg plain "$(section "$f" Plain)" \
    --arg in_practice "$(section "$f" "In practice")" \
    --argjson sources "$(fm_sources_json "$f")" '
    def paras: split("\n\n") | map(gsub("\n"; " ") | gsub("^\\s+|\\s+$"; "")) | map(select(. != ""));
    { term: $term }
    + (if $type != ""        then { type: $type }                              else {} end)
    + (if $metaphor != ""    then { metaphor: ($metaphor | paras | join("\n\n")) } else {} end)
    + (if $plain != ""       then { plain: ($plain | paras | join("\n\n")) }   else {} end)
    + (if $in_practice != "" then { in_practice: ($in_practice | paras) }      else {} end)
    + (if ($sources | length) > 0 then { sources: $sources }                   else {} end)
  ' >>"$TMP"
done

jq -s 'sort_by(.term)' "$TMP" >"$OUT"
printf 'wrote %s (%s entries)\n' "$OUT" "$(jq 'length' "$OUT")"
