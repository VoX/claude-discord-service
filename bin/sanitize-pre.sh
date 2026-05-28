#!/bin/bash
# ExecStartPre hook for claude-discord@.service — sanitizes orphaned
# `thinking`/`redacted_thinking` blocks from the active session transcript
# before claude --resume replays it. Workaround for anthropics/claude-code#63147
# (the 400 "thinking blocks cannot be modified" loop that bricks resume after
# any mid-turn interruption).
#
# Scope: ONLY the active session's transcript (derived from BOT_SESSION_NAME
# + the WorkingDirectory-encoded project dir Claude Code uses). Earlier
# versions of this script walked every project under $CLAUDE_CONFIG_DIR/
# projects/, which is overbroad — any stray dev claude in another worktree
# would get its transcript mutated under it. The session-specific version
# touches only the one .jsonl that `claude --resume <session>` will replay.
#
# How Claude Code names project dirs: it replaces every `/` in the absolute
# WorkingDirectory path with `-`. So WorkingDirectory=/home/foo/claude-discord/bar
# → projects/-home-foo-claude-discord-bar/. The session's transcript is the
# .jsonl file in that dir whose mtime is most recent.
#
# Exit codes: always 0. The python sanitizer's "nothing to do" exit 1 and
# "real failure" exit 3 are both swallowed — neither should block service
# start. A real failure is logged to stderr (which lands in journalctl).

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

# Derive the active session's project dir from WorkingDirectory.
# systemd doesn't expose WorkingDirectory as an env var to ExecStartPre,
# but we know the convention: WorkingDirectory=%h/claude-discord/%i, and
# %i == BOT_SESSION_NAME by default (or whatever BOT_SESSION_NAME is set
# to in .bot.env). So the project dir is the slash-to-dash encoding of
# $HOME/claude-discord/$BOT_SESSION_NAME — but only when the resume target
# matches the unit instance. If BOT_SESSION_NAME doesn't match the instance,
# the operator overrode it and we fall back to walking all project dirs
# (preserving the old broad behavior so we don't silently miss the target).
encode_path() {
  # Replace every / with -.
  printf '%s' "$1" | tr '/' '-'
}

ACTIVE_DIR=""
if [ -n "${BOT_SESSION_NAME:-}" ]; then
  CANDIDATE="$PROJECTS_DIR/$(encode_path "$HOME/claude-discord/$BOT_SESSION_NAME")"
  if [ -d "$CANDIDATE" ]; then
    ACTIVE_DIR="$CANDIDATE"
  fi
fi

# Build the list of dirs to scan. Targeted mode = the one active dir; fallback
# = every project dir.
DIRS=()
if [ -n "$ACTIVE_DIR" ]; then
  DIRS+=("$ACTIVE_DIR")
  echo "sanitize-pre: targeting active session dir $(basename "$ACTIVE_DIR")" >&2
else
  echo "sanitize-pre: active session dir not found, falling back to all project dirs" >&2
  while IFS= read -r -d '' d; do
    DIRS+=("$d")
  done < <(find "$PROJECTS_DIR" -mindepth 1 -maxdepth 1 -type d -print0)
fi

# For each dir, find the most-recently-modified .jsonl and sanitize it.
# Use find -printf %T@ for the mtime so we don't parse `ls` output (filenames
# with newlines, leading-dash, locale-sorted weirdness).
for dir in "${DIRS[@]}"; do
  [ -d "$dir" ] || continue
  newest=$(
    find "$dir" -mindepth 1 -maxdepth 1 -type f -name '*.jsonl' \
      -printf '%T@\t%p\0' 2>/dev/null \
      | sort -z -rn \
      | head -z -n 1 \
      | tr -d '\0' \
      | cut -f2-
  )
  [ -z "$newest" ] && continue
  # Sanitizer exits 1 when nothing to strip and 3 on real failure — neither
  # should block service start. Suppress with ||true.
  python3 "$SANITIZER" "$newest" || true
done

exit 0
