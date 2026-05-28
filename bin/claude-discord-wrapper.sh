#!/bin/bash
# Wrapper entrypoint for the claude-discord systemd service.
#
# Validates the per-instance config, acquires an exclusive flock so two
# wrappers can't race the same `claude --resume <session>` transcript, then
# execs the expect helper that does the actual spawn + dev-channels-warning
# auto-confirm dance.
#
# The lock (fd 9 → $CLAUDE_CONFIG_DIR/wrapper.lock) is held for the entire
# lifetime of the wrapper. `exec expect …` replaces this bash process with
# expect, which inherits fd 9 via execve and keeps the lock held until it
# itself exits (i.e. until claude exits). Two-second flock check, no retry —
# systemd Restart=always handles re-launch; the unit's StartLimit caps the
# retry storm if the lock contention is persistent.
#
# Config (from systemd Environment= / EnvironmentFile=~/.bot.env):
#
#   BOT_SESSION_NAME            required  claude --resume session name
#   BOT_PLUGINS                 optional  space-separated plugin specs
#   WARNING_AUTOCONFIRM_SLEEP   optional  seconds before sending Enter to
#                                         dismiss the dev-channels prompt
#                                         (default 8; bump to 30 on slow
#                                          cold-boot boxes like Strix Halo)
#   CLAUDE_CONFIG_DIR           optional  defaults to ~/.claude; used for
#                                         the lockfile location

set -euo pipefail

# Fail fast on missing session name.
: "${BOT_SESSION_NAME:?BOT_SESSION_NAME required — set it in ~/.bot.env}"

# Validate the session name. We splat it into argv, into a lockfile path, and
# (downstream in expect) into a Tcl string — the safest answer is to reject
# anything that isn't ASCII alphanumeric plus `_.-`. Anything weirder than
# that has no business being a claude session name anyway.
if [[ ! "$BOT_SESSION_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "claude-discord-wrapper: BOT_SESSION_NAME must match ^[A-Za-z0-9._-]+\$, got '$BOT_SESSION_NAME'" >&2
  exit 2
fi

# Acquire exclusive non-blocking flock. The fd is held for our process
# lifetime; on exec'ing into expect below, expect inherits the open fd and
# the kernel keeps the lock for the inheriting process. When expect exits,
# the kernel closes its fds and the lock is released — next wrapper start
# can claim it.
LOCK_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
mkdir -p "$LOCK_DIR"
LOCKFILE="$LOCK_DIR/wrapper.lock"
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  # Record who holds it for diagnostics — best-effort, may race.
  HOLDER="$(cat "$LOCKFILE" 2>/dev/null || true)"
  echo "claude-discord-wrapper: $LOCKFILE held — refusing to spawn duplicate ($HOLDER)" >&2
  exit 2
fi
# Stamp the lockfile so the next wrapper's diagnostic message names us.
printf 'pid=%s host=%s ts=%s\n' "$$" "$(hostname)" "$(date -u +%FT%TZ)" >&9

# Hand off to the expect spawn helper. fd 9 (the lock) is preserved across
# exec, so the lock stays held until the expect process (and the claude it
# spawns + interacts with) exits.
exec /usr/bin/expect -f "$(dirname "$0")/claude-discord-spawn.expect"
