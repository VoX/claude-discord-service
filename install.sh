#!/usr/bin/env bash
# Set up a claude-discord@<instance> bot under the current user.
#
# Usage:   install.sh <instance-name>
# Example: install.sh tinyclaw
#
# What it does:
#   1. Symlinks systemd/claude-discord@.service into ~/.config/systemd/user/
#      (template unit — one symlink covers all instances).
#   2. Creates ~/claude-discord/<instance>/{logs,.claude}/.
#   3. Seeds ~/claude-discord/<instance>/.bot.env from bot.env.example (0600)
#      if that file doesn't already exist.
#   4. Seeds ~/claude-discord/<instance>/.claude/CLAUDE.md from
#      CLAUDE.md.example (generic comms rules + blank personality).
#   5. Seeds ~/claude-discord/<instance>/.claude/settings.json with
#      skipDangerousModePermissionPrompt=true.
#   6. Seeds ~/claude-discord/<instance>/.claude/.claude.json with
#      hasCompletedOnboarding=true so the first `claude` launch under
#      this CLAUDE_CONFIG_DIR goes straight to /login instead of the
#      onboarding/picker flow.
#   7. Adds the vox-plugins marketplace and installs discord + scheduler
#      under the per-instance CLAUDE_CONFIG_DIR.
#   8. Seeds an empty claude session named <instance> so the bot can
#      --resume <instance> after the user logs in.
#   9. Prints the daemon-reload + enable commands for this instance.
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
         "$INSTANCE_DIR/logs" \
         "$CLAUDE_CONFIG_DIR"

# Seed a minimal settings.json. The only thing we set is
# skipDangerousModePermissionPrompt — lets the TUI accept the
# --dangerously-load-development-channels prompt under systemd without
# a blocking confirmation. Plugin enables + marketplace entries get
# written by the `claude plugin` CLI further down.
SETTINGS_DST="$CLAUDE_CONFIG_DIR/settings.json"
if [[ ! -e "$SETTINGS_DST" ]]; then
    cat > "$SETTINGS_DST" <<'JSON'
{
  "skipDangerousModePermissionPrompt": true
}
JSON
    echo "seeded $SETTINGS_DST"
else
    echo "$SETTINGS_DST already exists — leaving it alone"
fi

# Seed a minimal .claude.json. Without this, claude launched under a fresh
# CLAUDE_CONFIG_DIR treats the instance as a brand-new user — skips
# /discord:configure etc. and drops straight to the login picker on the
# first run. hasCompletedOnboarding=true + lastOnboardingVersion pinned to
# the current CLI version is enough to route /login like a normal account.
CLAUDE_JSON_DST="$CLAUDE_CONFIG_DIR/.claude.json"
if [[ ! -e "$CLAUDE_JSON_DST" ]]; then
    CLAUDE_VERSION="$(claude --version 2>/dev/null | awk '{print $1}' || true)"
    CLAUDE_VERSION="${CLAUDE_VERSION:-0.0.0}"
    cat > "$CLAUDE_JSON_DST" <<JSON
{
  "hasCompletedOnboarding": true,
  "lastOnboardingVersion": "$CLAUDE_VERSION",
  "firstStartTime": "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
}
JSON
    chmod 600 "$CLAUDE_JSON_DST"
    echo "seeded $CLAUDE_JSON_DST (onboarding bypass)"
else
    echo "$CLAUDE_JSON_DST already exists — leaving it alone"
fi

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

# Seed a starter CLAUDE.md. Goes at the CLAUDE_CONFIG_DIR root, which
# claude auto-loads as user-level memory — no --add-dir needed. Content
# is generic comms rules + formatting guidance with a blank Personality
# section for the user to fill in.
PERSONALITY_SRC="$REPO_DIR/CLAUDE.md.example"
PERSONALITY_DST="$CLAUDE_CONFIG_DIR/CLAUDE.md"
if [[ ! -e "$PERSONALITY_DST" ]]; then
    sed "s/<<INSTANCE>>/$INSTANCE/g" "$PERSONALITY_SRC" > "$PERSONALITY_DST"
    echo "seeded $PERSONALITY_DST from template"
else
    echo "$PERSONALITY_DST already exists — leaving it alone"
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

# --- seed a minimal claude session named <instance> ---------------------
# `claude --resume <instance>` matches on the first-line `custom-title`
# record of each session JSONL. Seeding one here means the systemd unit
# can resume immediately after the user configures credentials — no
# manual `claude -n <instance>` step required.
#
# Encoded project path mirrors claude's own scheme: the WorkingDirectory
# ('/home/ec2-user/claude-discord/<instance>') with '/' -> '-', leading
# slash producing the leading '-'.
SESSION_PROJECT_DIR="$CLAUDE_CONFIG_DIR/projects/${INSTANCE_DIR//\//-}"
mkdir -p "$SESSION_PROJECT_DIR"
shopt -s nullglob
existing_sessions=("$SESSION_PROJECT_DIR"/*.jsonl)
shopt -u nullglob
if (( ${#existing_sessions[@]} == 0 )); then
    SEED_UUID="$(cat /proc/sys/kernel/random/uuid)"
    SEED_FILE="$SESSION_PROJECT_DIR/$SEED_UUID.jsonl"
    printf '{"type":"custom-title","customTitle":"%s","sessionId":"%s"}\n' \
        "$INSTANCE" "$SEED_UUID" > "$SEED_FILE"
    echo "seeded empty session '$INSTANCE' -> $SEED_FILE"
else
    echo "session files already present in $SESSION_PROJECT_DIR — skipping seed"
fi

cat <<EOM

Next steps for instance '$INSTANCE':
  1. Log in to Anthropic and register the Discord bot under the
     per-instance config dir:
       CLAUDE_CONFIG_DIR=$CLAUDE_CONFIG_DIR claude
         > /login
         > /discord:configure
         > (exit when done)
  2. Optional: edit $PERSONALITY_DST to give the bot a personality
  3. Optional: edit $ENV_DST to override defaults (model, plugins, etc.)
  4. systemctl --user daemon-reload
  5. systemctl --user enable --now claude-discord@$INSTANCE
  6. journalctl --user -u claude-discord@$INSTANCE -f

Logs stream to $INSTANCE_DIR/logs/claude-discord{,.error}.log as well.
EOM
