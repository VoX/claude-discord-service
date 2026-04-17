# claude-discord-service

A systemd **user template unit** + wrapper script for running one or more
persistent Claude Code sessions wired up as Discord bots. Companion to the
[`vox-plugins`](https://github.com/VoX/vox-plugins) `discord` and `scheduler` plugins.

## What this is

For each bot you run, one long-lived `claude --resume <name>` session,
kept alive under a `screen` wrapper, reconnected to Discord via an MCP
plugin. The unit is a systemd **template** (`claude-discord@.service`),
so one file on disk can spawn any number of bot instances — each with
its own working directory, env file, personality folder, logs, and
screen session.

All per-bot config lives under `~/claude-discord/<instance>/` (not in
this repo), so the unit file is generic and safe to commit.

## What this is not

- Not a plugin installer. The Discord/scheduler plugins live in
  [`vox-plugins`](https://github.com/VoX/vox-plugins); install them separately.
- Not the Discord bot token store. That lives in
  `~/.claude/channels/discord/.env` and is managed by `/discord:configure`.
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
2. Creates `~/claude-discord/<instance>/{claude-personality,logs}/`.
3. Seeds `~/claude-discord/<instance>/.bot.env` from `bot.env.example`
   (mode 0600) if it doesn't already exist.
4. Prints the remaining manual steps.

Then:

```bash
# make sure the claude session exists (named the same as the instance
# by default) — create it once if not:
claude -n <instance>

# optional tweaks:
$EDITOR ~/claude-discord/<instance>/.bot.env                      # override defaults
# drop a CLAUDE.md into ~/claude-discord/<instance>/claude-personality/

systemctl --user daemon-reload
systemctl --user enable --now claude-discord@<instance>
journalctl --user -u claude-discord@<instance> -f
```

## Per-instance layout

Each instance gets its own folder:

```
~/claude-discord/<instance>/
├── .bot.env                 # per-instance config (0600, gitignored)
├── claude-personality/      # CLAUDE.md + any files mounted via --add-dir
└── logs/
    ├── claude-discord.log
    └── claude-discord.error.log
```

The unit derives per-instance paths from `%i` (the part after `@` in
the unit name), so `claude-discord@tinyclaw` reads
`~/claude-discord/tinyclaw/.bot.env` and writes to
`~/claude-discord/tinyclaw/logs/`.

## Configuration (`~/claude-discord/<instance>/.bot.env`)

Systemd reads this file as plain `KEY=VALUE` lines — no shell expansion.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `BOT_SESSION_NAME` | no | `<instance>` | `claude --resume <name>` target. Defaults to the instance name; override if you want the service to resume a differently-named session. Must already exist. |
| `BOT_PLUGINS` | no | unset | Space-separated specs for `--dangerously-load-development-channels`. |
| `SCREEN_SESSION` | no | `claude-discord-<instance>` | Screen session name the wrapper runs under. Default is already unique per instance; override only if you need a specific name. |
| `BOT_ADD_DIR` | no | `~/claude-discord/<instance>/claude-personality` | Extra dir mounted via `--add-dir`. |
| `ANTHROPIC_MODEL` | no | Claude Code default | Override the model. |
| `CLAUDE_CODE_SUBAGENT_MODEL` | no | Claude Code default | Override subagent model. |
| `CLAUDE_CODE_EFFORT_LEVEL` | no | Claude Code default | `low` / `medium` / `high` / `max`. |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | no | Claude Code default | Tokens-before-compact threshold. |

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

## Upgrading

```bash
cd ~/projects/claude-discord-service
git pull
systemctl --user daemon-reload                           # only if the unit changed
systemctl --user restart 'claude-discord@*'              # restart all instances
# or: systemctl --user restart claude-discord@<instance> # just one
```

The `install.sh` symlink means `git pull` is enough; no re-copy needed.

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
