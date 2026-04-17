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

# Per-instance CLAUDE_CONFIG_DIR — keeps each bot's sessions, plugins,
# and channel state isolated from the user's own ~/.claude/.
export CLAUDE_CONFIG_DIR="$INSTANCE_DIR/.claude"

mkdir -p "$HOME/.config/systemd/user" \
         "$INSTANCE_DIR/claude-personality" \
         "$INSTANCE_DIR/logs" \
         "$CLAUDE_CONFIG_DIR"

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
    echo "seeded $ENV_DST from template (0600) — fine to leave untouched if defaults are OK"
else
    echo "$ENV_DST already exists — leaving it alone"
fi

# --- vox-plugins marketplace + plugins (opinionated) --------------------
# Idempotent: only adds / installs what's missing.

if command -v claude >/dev/null 2>&1; then
    if ! claude plugin marketplace list 2>/dev/null | grep -qE '^\s*❯?\s*vox-plugins\b'; then
        echo "adding vox-plugins marketplace"
        claude plugin marketplace add VoX/vox-plugins
    else
        echo "vox-plugins marketplace already configured"
    fi

    for PLUGIN in discord scheduler; do
        if ! claude plugin list 2>/dev/null | grep -qE "^\s*❯?\s*$PLUGIN@vox-plugins\b"; then
            echo "installing $PLUGIN@vox-plugins"
            claude plugin install "$PLUGIN@vox-plugins"
        else
            echo "$PLUGIN@vox-plugins already installed"
        fi
    done
else
    echo "warning: 'claude' CLI not on PATH — skipping marketplace/plugin setup"
    echo "         install claude, then re-run this script to finish plugin setup"
fi

cat <<EOM

Next steps for instance '$INSTANCE':
  1. Make sure a claude session named '$INSTANCE' exists
     (create with 'claude -n $INSTANCE' if not)
  2. Optional: drop a CLAUDE.md into $INSTANCE_DIR/claude-personality/
  3. Optional: edit $ENV_DST to override defaults (model, plugins, etc.)
  4. systemctl --user daemon-reload
  5. systemctl --user enable --now claude-discord@$INSTANCE
  6. journalctl --user -u claude-discord@$INSTANCE -f

Logs stream to $INSTANCE_DIR/logs/claude-discord{,.error}.log as well.
EOM
