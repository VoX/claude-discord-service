#!/bin/bash
# ExecStartPre hook for claude-discord@.service — sanitizes orphaned
# `thinking`/`redacted_thinking` blocks from the active session transcript
# before claude --resume replays it. Workaround for anthropics/claude-code#63147
# (the 400 "thinking blocks cannot be modified" loop that bricks resume after
# any mid-turn interruption).
#
# What it does:
#   - For each project dir under $CLAUDE_CONFIG_DIR/projects/, find the newest
#     .jsonl and run the sanitizer over it.
#   - The sanitizer is idempotent — no-op if no thinking blocks found, exits 1
#     in that case. We swallow non-zero exits so a clean transcript doesn't
#     block service startup.
#
# Cost: ~50ms per transcript on cold start, zero in steady state.
set -u

CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
PROJECTS_DIR="$CLAUDE_DIR/projects"
SANITIZER="$(dirname "$0")/sanitize_thinking_blocks.py"

if [ ! -d "$PROJECTS_DIR" ]; then
  echo "sanitize-pre: $PROJECTS_DIR not found, skipping" >&2
  exit 0
fi
if [ ! -f "$SANITIZER" ]; then
  echo "sanitize-pre: $SANITIZER not found, skipping" >&2
  exit 0
fi

for dir in "$PROJECTS_DIR"/*/; do
  [ -d "$dir" ] || continue
  newest="$(ls -t "$dir"*.jsonl 2>/dev/null | head -1)"
  [ -z "$newest" ] && continue
  # Sanitizer exits 1 when nothing to strip — that's normal, suppress.
  python3 "$SANITIZER" "$newest" || true
done

exit 0
