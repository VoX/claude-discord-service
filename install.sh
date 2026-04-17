#!/usr/bin/env bash
# Set up a claude-discord@<instance> bot under the current user.
#
# Usage:   install.sh <instance-name>
# Example: install.sh tinyclaw
#
# What it does:
#   1. Symlinks systemd/claude-discord@.service into ~/.config/systemd/user/
#      (template unit — one symlink covers all instances).
#   2. Creates ~/claude-discord/<instance>/{claude-personality,logs}/ .
#   3. Seeds ~/claude-discord/<instance>/.bot.env from bot.env.example (0600)
#      if that file doesn't already exist.
#   4. Prints the daemon-reload + enable commands for this instance.
#
# Idempotent — safe to re-run with the same <instance-name>. Does NOT
# restart the service; you decide when to take a bot down.

set -euo pipefail

if [[ $# -lt 1 || -z "${1:-}" ]]; then
    echo "usage: $0 <instance-name>" >&2
    echo "example: $0 tinyclaw" >&2
    exit 2
fi

INSTANCE="$1"
if [[ ! "$INSTANCE" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    echo "instance name must match [a-z0-9][a-z0-9_-]* (got: $INSTANCE)" >&2
    exit 2
fi

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
UNIT_SRC="$REPO_DIR/systemd/claude-discord@.service"
UNIT_DST="$HOME/.config/systemd/user/claude-discord@.service"
ENV_SRC="$REPO_DIR/bot.env.example"

INSTANCE_DIR="$HOME/claude-discord/$INSTANCE"
ENV_DST="$INSTANCE_DIR/.bot.env"

mkdir -p "$HOME/.config/systemd/user" \
         "$INSTANCE_DIR/claude-personality" \
         "$INSTANCE_DIR/logs"

if [[ -L "$UNIT_DST" || -f "$UNIT_DST" ]]; then
    existing="$(readlink -f "$UNIT_DST" 2>/dev/null || echo "$UNIT_DST")"
    if [[ "$existing" == "$UNIT_SRC" ]]; then
        echo "template unit already points at $UNIT_SRC"
    else
        backup="$UNIT_DST.bak.$(date -u +%Y%m%dT%H%M%SZ)"
        mv "$UNIT_DST" "$backup"
        ln -s "$UNIT_SRC" "$UNIT_DST"
        echo "replaced existing template unit (backed up to $backup)"
    fi
else
    ln -s "$UNIT_SRC" "$UNIT_DST"
    echo "linked template unit: $UNIT_DST -> $UNIT_SRC"
fi

if [[ ! -e "$ENV_DST" ]]; then
    cp "$ENV_SRC" "$ENV_DST"
    chmod 600 "$ENV_DST"
    echo "seeded $ENV_DST from template (0600) — edit BOT_SESSION_NAME before starting"
else
    echo "$ENV_DST already exists — leaving it alone"
fi

cat <<EOM

Next steps for instance '$INSTANCE':
  1. Edit $ENV_DST (at minimum set BOT_SESSION_NAME)
  2. Drop a CLAUDE.md into $INSTANCE_DIR/claude-personality/ (optional)
  3. systemctl --user daemon-reload
  4. systemctl --user enable --now claude-discord@$INSTANCE
  5. journalctl --user -u claude-discord@$INSTANCE -f

Logs stream to $INSTANCE_DIR/logs/claude-discord{,.error}.log as well.
EOM
