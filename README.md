# claude-discord-service

A systemd **user template unit** + wrapper script for running one or more
persistent Claude Code sessions wired up as Discord bots. Companion to the
[`vox-plugins`](https://github.com/VoX/vox-plugins) `discord` and `scheduler` plugins.

## What this is

For each bot you run, one long-lived `claude --resume <name>` session,
kept alive under a `screen` wrapper, reconnected to Discord via an MCP
plugin. The unit is a systemd **template** (`claude-discord@.service`),
so one file on disk can spawn any number of bot instances — each with
its own working directory, env file, logs, and screen session.

All per-bot config lives under `~/claude-discord/<instance>/` (not in
this repo), so the unit file is generic and safe to commit.

## What this is not

- Not a Discord bot. The bot-side logic (MCP server, event handlers,
  Allow/Deny permission prompts) lives in the
  [`vox-plugins`](https://github.com/VoX/vox-plugins) `discord` plugin —
  this repo just wires up systemd to launch a long-running Claude session
  that loads it. `install.sh` does handle the plugin install for you.
- Not a multi-user orchestrator. One unit template runs any number of
  instances for one user.

## Install

```bash
git clone https://github.com/VoX/claude-discord-service ~/projects/claude-discord-service
~/projects/claude-discord-service/install.sh <instance-name>
```

Example: `install.sh tinyclaw` sets up an instance called `tinyclaw`.
Re-run with a different name to add more bots later.

The unit hardcodes `%h/projects/claude-discord-service/` as the repo
location. If you clone elsewhere, edit the `ExecStart=` path in
`systemd/claude-discord@.service` to match.

`install.sh <instance>` is idempotent. It:

1. Symlinks `systemd/claude-discord@.service` into `~/.config/systemd/user/`
   (one template symlink covers every instance; backs up any existing file).
2. Creates `~/claude-discord/<instance>/{logs,.claude}/`.
3. Seeds `~/claude-discord/<instance>/.bot.env` from `bot.env.example`
   (mode 0600) if it doesn't already exist.
4. Seeds `~/claude-discord/<instance>/.claude/CLAUDE.md` from
   `CLAUDE.md.example` — generic communication rules plus a placeholder
   "Personality" section for you to customize. Claude auto-loads this as
   user-level memory (it's the per-instance `CLAUDE_CONFIG_DIR`), so no
   `--add-dir` plumbing.
5. Seeds `~/claude-discord/<instance>/.claude/settings.json` with
   `skipDangerousModePermissionPrompt: true` so the TUI accepts the
   dev-channels warning under systemd.
6. Seeds `~/claude-discord/<instance>/.claude/.claude.json` with
   `hasCompletedOnboarding: true` (plus `lastOnboardingVersion` pinned to
   the current `claude --version`) so the first `claude` launch under the
   fresh `CLAUDE_CONFIG_DIR` goes straight to `/login` instead of the
   onboarding picker.
7. Adds the [`vox-plugins`](https://github.com/VoX/vox-plugins) marketplace
   and installs the `discord` + `scheduler` plugins under the per-instance
   `CLAUDE_CONFIG_DIR` (skipped if already present, or if `claude` isn't on
   `PATH`).
8. Seeds an empty claude session named `<instance>` so the bot can
   `--resume <instance>` immediately after you log in.
9. Prints the remaining manual steps.

Then log in and configure the Discord bot under the per-instance config
dir, and start the service:

```bash
# Log in to Anthropic + register the Discord bot token.
# CLAUDE_CONFIG_DIR scopes both to the instance's .claude/ folder.
CLAUDE_CONFIG_DIR=~/claude-discord/<instance>/.claude claude
  > /login
  > /discord:configure
  > (exit)

# optional tweaks:
$EDITOR ~/claude-discord/<instance>/.bot.env                   # override defaults
$EDITOR ~/claude-discord/<instance>/.claude/CLAUDE.md          # give the bot a personality

systemctl --user daemon-reload
systemctl --user enable --now claude-discord@<instance>
journalctl --user -u claude-discord@<instance> -f
```

## Per-instance layout

Each instance gets its own folder:

```
~/claude-discord/<instance>/
├── .bot.env                 # per-instance config (0600, gitignored)
├── .claude/                 # per-instance CLAUDE_CONFIG_DIR — fully
│   │                        #   isolated from the user's own ~/.claude/
│   ├── CLAUDE.md            # personality + comms rules (auto-loaded)
│   ├── settings.json        # skipDangerousModePermissionPrompt
│   ├── .claude.json         # onboarding-bypass markers
│   ├── .credentials.json    # Anthropic OAuth tokens (written by /login)
│   ├── channels/            # discord token + allowlist + scheduler jobs
│   ├── plugins/             # marketplace cache + installed plugin registry
│   └── projects/            # session jsonl history
└── logs/
    ├── claude-discord.log
    └── claude-discord.error.log
```

The unit derives per-instance paths from `%i` (the part after `@` in
the unit name), so `claude-discord@tinyclaw` reads
`~/claude-discord/tinyclaw/.bot.env`, stores its Claude state under
`~/claude-discord/tinyclaw/.claude/`, and writes to
`~/claude-discord/tinyclaw/logs/`.

## Configuration (`~/claude-discord/<instance>/.bot.env`)

Systemd reads this file as plain `KEY=VALUE` lines — no shell expansion.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `BOT_SESSION_NAME` | no | `<instance>` | `claude --resume <name>` target. Defaults to the instance name; override if you want the service to resume a differently-named session. Must already exist. |
| `BOT_PLUGINS` | no | `plugin:discord@vox-plugins plugin:scheduler@vox-plugins` | Space-separated specs for `--dangerously-load-development-channels`. Shipped example pre-enables the vox-plugins discord + scheduler plugins; clear the line to disable. |
| `SCREEN_SESSION` | no | `claude-discord-<instance>` | Screen session name the wrapper runs under. Default is already unique per instance; override only if you need a specific name. |
| `ANTHROPIC_MODEL` | no | `claude-opus-4-7` | Shipped example pins Opus 4.7; comment out for the Claude Code default. |
| `CLAUDE_CODE_SUBAGENT_MODEL` | no | `claude-opus-4-7` | Shipped example pins Opus 4.7 for subagents too. |
| `CLAUDE_CODE_EFFORT_LEVEL` | no | `max` | `low` / `medium` / `high` / `max`. |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | no | `750000` | Tokens-before-compact threshold. |

See `bot.env.example` for the commented template.

## Running multiple instances

```bash
install.sh tinyclaw
install.sh dweller
systemctl --user enable --now claude-discord@tinyclaw claude-discord@dweller
```

Each bot has its own config, screen session, and logs. The unit caps
memory at `MemoryMax=8G` per instance — tune that in the unit if you
plan on running more than two bots on a 16G box.

## Files

```
systemd/claude-discord@.service   # template unit (uses %i for instance name)
bin/claude-discord-wrapper.sh     # expect(1) wrapper: spawns claude, accepts the dev-channels prompt
bot.env.example                   # template for per-instance .bot.env
CLAUDE.md.example                 # starter per-instance CLAUDE.md (comms rules + blank personality)
install.sh                        # per-instance setup helper
```

## How the wrapper handles the dev-channels prompt

`--dangerously-load-development-channels` triggers an interactive warning
prompt that expects a keypress. Since we're running headless under
systemd, the wrapper is an `expect(1)` script that spawns `claude`, sleeps
three seconds to let the TUI finish rendering, sends a single `\r`, then
hands control off to the screen session via `interact`. The sleep is
dumb-but-reliable — escape codes in the TUI make text-matching brittle.

If you don't use dev-channel plugins, the sleep+send still runs but is
harmless (no matching prompt to answer).

## Permission prompts

The unit ships `DISCORD_AUTO_ALLOW_PERMISSIONS=1`, which tells the
`discord` plugin to skip the Allow/Deny DM flow for `permission_request`
notifications and auto-approve every tool call. The assumption is that
gating lives in the bot's persona rules (its `CLAUDE.md`) rather than
the OS-prompt layer — the DM flow gets noisy fast for a long-running
agent. Each auto-allow still writes an audit line to stderr (visible in
`journalctl --user -u claude-discord@<instance>`).

If you'd rather route every tool call through Discord DMs with
Allow/Deny buttons, comment out the `Environment=DISCORD_AUTO_ALLOW_PERMISSIONS=1`
line in `systemd/claude-discord@.service` (or override it with
`DISCORD_AUTO_ALLOW_PERMISSIONS=0` in `.bot.env`) and restart the
instance. Requires the `discord` plugin at version `0.1.18` or later.

## Upgrading

Upgrading this repo (the unit + wrapper):

```bash
cd ~/projects/claude-discord-service
git pull
systemctl --user daemon-reload                           # only if the unit changed
systemctl --user restart 'claude-discord@*'              # restart all instances
# or: systemctl --user restart claude-discord@<instance> # just one
```

The `install.sh` symlink means `git pull` is enough; no re-copy needed.

Upgrading the plugins is separate — the `vox-plugins` marketplace is
pulled into each instance's `CLAUDE_CONFIG_DIR` and pinned there, so
`git pull` on this repo doesn't move them. Per instance:

```bash
CLAUDE_CONFIG_DIR=~/claude-discord/<instance>/.claude \
  claude plugin update discord@vox-plugins scheduler@vox-plugins
systemctl --user restart claude-discord@<instance>
```

## Uninstall

```bash
# one instance:
systemctl --user disable --now claude-discord@<instance>
rm -rf ~/claude-discord/<instance>

# remove the template entirely (takes all instances down):
systemctl --user disable --now 'claude-discord@*'
rm ~/.config/systemd/user/claude-discord@.service
# optional: rm -rf ~/projects/claude-discord-service ~/claude-discord
```

## License

MIT — see [LICENSE](./LICENSE).
