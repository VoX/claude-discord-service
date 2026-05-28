#!/usr/bin/expect -f
# Wrapper for the claude-discord systemd service.
#
# Runs `claude` under a screen session with the right flags, then sends Enter
# once to auto-accept the "development channels" warning prompt. Escape codes
# in the TUI make reliable text-matching unreliable, so we use a timed send.
#
# Config comes from the environment (populated by systemd via ~/.bot.env):
#
#   BOT_SESSION_NAME  required  claude --resume session name
#   BOT_PLUGINS       optional  space-separated plugin specs for
#                               --dangerously-load-development-channels
#                               e.g. "plugin:discord@vox-plugins plugin:scheduler@vox-plugins"
#
# Exits 2 if BOT_SESSION_NAME isn't set — a misconfigured env should fail
# fast, not silently spawn an anonymous claude session.

set timeout -1

if {![info exists ::env(BOT_SESSION_NAME)] || $::env(BOT_SESSION_NAME) eq ""} {
    puts stderr "claude-discord-wrapper: BOT_SESSION_NAME is required (set it in ~/.bot.env)"
    exit 2
}

# Duplicate-session guard. If a `claude --resume <BOT_SESSION_NAME>` is already
# running (typically a stray manual launch outside the systemd cgroup, which
# KillMode=control-group can't clean up), refuse to spawn a second one. Two
# concurrent claudes against the same session race the transcript .jsonl —
# both write, both replay, mutual orphan-block production. Caught 2026-05-28
# when tinydos had a manual screen from May 27 coexisting with the systemd
# one for 24+ hours; the resulting transcript-race plausibly contributed to
# the thinking-block accumulation that wedged her.
set existing [exec sh -c "pgrep -f \"^/.*/claude --resume $::env(BOT_SESSION_NAME) \" || true"]
if {$existing ne ""} {
    puts stderr "claude-discord-wrapper: claude --resume $::env(BOT_SESSION_NAME) already running (pid $existing), refusing to spawn duplicate"
    exit 2
}

set args [list --resume $::env(BOT_SESSION_NAME)]

if {[info exists ::env(BOT_PLUGINS)] && $::env(BOT_PLUGINS) ne ""} {
    lappend args --dangerously-load-development-channels
    foreach plugin [split $::env(BOT_PLUGINS)] {
        if {$plugin ne ""} { lappend args $plugin }
    }
}

lappend args --dangerously-skip-permissions --permission-mode bypassPermissions

spawn $::env(HOME)/.local/bin/claude {*}$args

# Wait for the TUI to finish rendering the warning prompt, then accept.
# 3s was too tight under systemd cold-start on this box — the prompt rendered
# but send "\r" fired before claude was ready to consume it. 8s is dumb but
# reliable; escape codes make text-matching brittle.
sleep 8
send "\r"

# Hand off to the screen session for the long run.
interact
