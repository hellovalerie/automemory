#!/usr/bin/env bash
# transcript-to-text.sh — convert a Claude Code transcript JSONL into readable text.
#
# Emits human/assistant *text* turns exactly as before (byte-identical — the
# update-append watermark in capture-worker.sh compares rendered prefixes), plus
# CONDENSED tool markers interleaved chronologically so the observer can see
# sub-agent dispatches and commands (issue #8):
#
#   [DISPATCH: <description or first ~15 words of prompt>]   Task/Agent tool_use
#   [AGENT RETURNED: <first ~25 words of result>]            Task/Agent tool_result
#   [BASH: <command ~80ch> -> <first output line|error exit N>]  at result time
#   [EDIT: <path>] / [WRITE: <path>]                          path only
#   [TOOL <name>]                                             any other tool
#
# Every marker is capped at ~200 chars; raw tool payloads are never emitted
# (they can be huge and can contain secrets). Thinking blocks and Claude Code's
# internal bookkeeping records are still dropped.
#
# Usage:  transcript-to-text.sh <transcript.jsonl>   (or pipe JSONL on stdin)
set -euo pipefail

src="${1:-/dev/stdin}"

# One JSON object per line, streamed through foreach so tool_result records can
# look up the name/command of the tool_use they answer (state = id -> summary).
# Dialogue rendering is unchanged from the pre-marker version:
#  - user   .message.content is usually a plain string (a real prompt); when it is
#            an array it is a tool_result batch, so we keep only its text items.
#  - assistant .message.content is an array of blocks; we keep type=="text" and
#            drop thinking; tool_use becomes a marker, never a payload.
jq -rnj '
  def clean: tostring | gsub("[[:space:][:cntrl:]]+"; " ") | sub("^ "; "") | sub(" $"; "");
  def clip($n): if length > $n then .[0:$n] + "…" else . end;
  def words($n): [scan("[^[:space:]]+")] as $w
    | ($w[0:$n] | join(" ")) + (if ($w|length) > $n then " …" else "" end);

  foreach inputs as $r ({};
    # -- state: remember each tool_use so its tool_result can be labeled -----
    if $r.type == "assistant" and (($r.message.content // null) | type) == "array" then
      reduce ($r.message.content[] | select(.type == "tool_use")) as $t (.;
        .[$t.id] = { name: $t.name,
                     cmd: (($t.input.command // "") | clean | clip(80)) })
    else . end;

    . as $st |

    # -- dialogue: byte-identical to the previous renderer --------------------
    ( if ($r.type == "user" or $r.type == "assistant") and $r.message != null then
        ($r.message.role // $r.type) as $role
        | $r.message.content as $c
        | ( if ($c|type) == "string" then $c
            else ($c | map(select(.type=="text") | .text) | join("\n\n"))
            end ) as $text
        | if ($text|length) > 0 then "### " + $role + "\n\n" + $text + "\n\n" else "" end
      else "" end )

    # -- markers for assistant tool_use blocks (Bash waits for its result) ----
    + ( if $r.type == "assistant" and (($r.message.content // null) | type) == "array" then
          [ $r.message.content[] | select(.type == "tool_use")
            | ( if .name == "Task" or .name == "Agent" then
                  "[DISPATCH: " + ((.input.description // ((.input.prompt // "") | words(15))) | clean) + "]"
                elif .name == "Bash" then empty
                elif .name == "Edit" or .name == "MultiEdit" or .name == "NotebookEdit" then
                  "[EDIT: " + ((.input.file_path // .input.notebook_path // "?") | clean) + "]"
                elif .name == "Write" then
                  "[WRITE: " + ((.input.file_path // "?") | clean) + "]"
                else
                  "[TOOL " + (.name | clean) + "]"
                end )
            | clip(200) + "\n\n"
          ] | join("")
        else "" end )

    # -- markers for tool_result blocks inside user records -------------------
    + ( if $r.type == "user" and (($r.message.content // null) | type) == "array" then
          [ $r.message.content[] | select(.type == "tool_result")
            | ($st[.tool_use_id] // {}) as $u
            | ( .content as $c
                | if ($c|type) == "string" then $c
                  elif ($c|type) == "array" then
                    ([$c[]? | select(.type=="text") | .text] | join("\n"))
                  else "" end ) as $out
            | ( if $u.name == "Task" or $u.name == "Agent" then
                  "[AGENT RETURNED: " + ($out | clean | words(25)) + "]"
                elif $u.name == "Bash" then
                  ( ($out | split("\n") | .[0] // "" | clean) as $line1
                    | if .is_error == true then
                        ( ($line1 | capture("^Exit code (?<n>[0-9]+)")? // null) as $m
                          | if $m != null then "error exit " + $m.n
                            else "error: " + ($line1 | clip(80)) end )
                      elif ($line1|length) == 0 then "(no output)"
                      else $line1 end ) as $res
                  | "[BASH: " + $u.cmd + " -> " + $res + "]"
                else empty end )
            | clip(200) + "\n\n"
          ] | join("")
        else "" end )
  )
' "$src"
