# claude-discord-service

A systemd user unit + wrapper script for running a persistent Claude Code
session wired up as a Discord bot. Companion to the [`vox-plugins`](https://github.com/VoX/vox-plugins)
`discord` and `scheduler` plugins.

## What this is

One long-lived `claude --resume <name>` session, kept alive under a `screen`
wrapper, reconnected to Discord via an MCP plugin. The systemd unit restarts
it on crash, caps memory, pipes stdout/stderr to append-only logs, and feeds
runtime flags in via `Environment=` lines.

All the values that change between users — session name, personality dir,
plugin list, model selection — live in `~/.bot.env` (not in this repo), so
the unit file itself is generic and safe to commit.

## What this is not

- Not a plugin installer. The Discord/scheduler plugins live in
  [`vox-plugins`](https://github.com/VoX/vox-plugins); install them separately.
- Not the Discord bot token store. That lives in
  `~/.claude/channels/discord/.env` and is managed by `/discord:configure`.
- Not a multi-user orchestrator. One unit runs one bot for one user.

## Install

```bash
git clone https://github.com/VoX/claude-discord-service ~/claude-discord-service
~/claude-discord-service/install.sh
```

`install.sh` is idempotent. It:

1. Symlinks `systemd/claude-discord.service` into `~/.config/systemd/user/`
   (backs up any existing file first).
2. Seeds `~/.bot.env` from `bot.env.example` if you don't already have one.
3. Creates the `logs/` directory.
4. Prints the remaining manual steps (edit the env file, daemon-reload,
   enable the unit).

Then:

```bash
$EDITOR ~/.bot.env                      # at minimum, set BOT_SESSION_NAME
systemctl --user daemon-reload
systemctl --user enable --now claude-discord
journalctl --user -u claude-discord -f
```

## Configuration (`~/.bot.env`)

Systemd reads this file as plain `KEY=VALUE` lines — no shell expansion.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `BOT_SESSION_NAME` | yes | — | `claude --resume <name>` target. Must already exist. |
| `SCREEN_SESSION` | no | `claude-discord` | Screen session name the wrapper runs under. Change if you run multiple bots on one machine. |
| `BOT_ADD_DIR` | no | unset | Extra directory mounted via `--add-dir` (e.g. a personality repo). |
| `BOT_PLUGINS` | no | unset | Space-separated specs for `--dangerously-load-development-channels`. |
| `ANTHROPIC_MODEL` | no | Claude Code default | Override the model. |
| `CLAUDE_CODE_SUBAGENT_MODEL` | no | Claude Code default | Override subagent model. |
| `CLAUDE_CODE_EFFORT_LEVEL` | no | Claude Code default | `low` / `medium` / `high` / `max`. |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | no | Claude Code default | Tokens-before-compact threshold. |

See `bot.env.example` for the commented template.

## Files

```
systemd/claude-discord.service    # the unit (uses %h, EnvironmentFile=-%h/.bot.env)
bin/claude-discord-wrapper.sh     # expect(1) wrapper: spawns claude, accepts the dev-channels prompt
bot.env.example                   # template for ~/.bot.env
install.sh                        # symlink + seed + logs-dir helper
logs/                             # runtime logs (gitignored)
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
cd ~/claude-discord-service
git pull
systemctl --user daemon-reload          # only if the unit file changed
systemctl --user restart claude-discord # if anything else changed
```

The `install.sh` symlink means `git pull` is enough; no re-copy needed.

## Uninstall

```bash
systemctl --user disable --now claude-discord
rm ~/.config/systemd/user/claude-discord.service
# optional: rm -rf ~/claude-discord-service ~/.bot.env
```

## License

MIT — see [LICENSE](./LICENSE).
