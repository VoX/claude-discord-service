#!/usr/bin/env bash
# Symlink the systemd user unit into ~/.config/systemd/user/, create the
# logs directory, and remind you about the env file + daemon-reload step.
#
# Idempotent — safe to re-run after pulling updates. Does NOT restart the
# service; you decide when to take the bot down.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
UNIT_SRC="$REPO_DIR/systemd/claude-discord.service"
UNIT_DST="$HOME/.config/systemd/user/claude-discord.service"
LOGS_DIR="$REPO_DIR/logs"
ENV_SRC="$REPO_DIR/bot.env.example"
ENV_DST="$HOME/.bot.env"

mkdir -p "$HOME/.config/systemd/user" "$LOGS_DIR"

if [[ -L "$UNIT_DST" || -f "$UNIT_DST" ]]; then
    existing="$(readlink -f "$UNIT_DST" 2>/dev/null || echo "$UNIT_DST")"
    if [[ "$existing" == "$UNIT_SRC" ]]; then
        echo "unit already points at $UNIT_SRC"
    else
        backup="$UNIT_DST.bak.$(date -u +%Y%m%dT%H%M%SZ)"
        mv "$UNIT_DST" "$backup"
        ln -s "$UNIT_SRC" "$UNIT_DST"
        echo "replaced existing unit (backed up to $backup)"
    fi
else
    ln -s "$UNIT_SRC" "$UNIT_DST"
    echo "linked unit: $UNIT_DST -> $UNIT_SRC"
fi

if [[ ! -e "$ENV_DST" ]]; then
    cp "$ENV_SRC" "$ENV_DST"
    chmod 600 "$ENV_DST"
    echo "seeded $ENV_DST from template (0600) — edit BOT_SESSION_NAME before starting"
else
    echo "$ENV_DST already exists — leaving it alone"
fi

cat <<EOM

Next steps:
  1. Edit $ENV_DST and set BOT_SESSION_NAME (at minimum)
  2. systemctl --user daemon-reload
  3. systemctl --user enable --now claude-discord
  4. journalctl --user -u claude-discord -f   # watch it come up

Logs stream to $LOGS_DIR/claude-discord{,.error}.log as well.
EOM
