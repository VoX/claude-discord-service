#!/usr/bin/env python3
"""
StopFailure hook: when a safeguard refusal ends the bot's turn, queue a
rewind past that turn and restart the bot so the rewind takes effect.

A refusal ends the turn as a synthetic API error (error "invalid_request",
stop_reason "refusal"), which fires StopFailure with the notice text in
`last_assistant_message`. This hook:

  1. ignores anything that is not a safeguard notice, and anything that is
     not the bot's main claude (a `claude -p` run started from inside the bot
     inherits its env and config dir, and must not restart the bot);
  2. writes $CLAUDE_CONFIG_DIR/flagged-turn.request for the ExecStartPre
     `drop_flagged_turn.py --from-marker`, which does the actual rewind;
  3. restarts the unit through a transient `systemd-run` timer, which lives
     outside the unit's cgroup and so survives the stop that kills this hook;
  4. then sleeps until the restart kills it. StopFailure is awaited, so
     holding it keeps claude from starting the next queued message on top of
     the flagged context in the seconds before the stop lands.

A breaker stops it after BREAKER_MAX recoveries in BREAKER_WINDOW seconds:
past that, something keeps re-triggering the flag and a restart loop would
not fix it. The bot is then left as it is, for a human.

Wire it in settings.json:
  "hooks": {"StopFailure": [{"matcher": "invalid_request", "hooks": [
      {"type": "command", "timeout": 120,
       "command": "python3 /path/to/bin/on_stop_failure.py"}]}]}
"""
import json
import os
import re
import subprocess
import sys
import time

MARKER_NAME = "flagged-turn.request"
STATE_NAME = "flagged-turn.recoveries"
LOG_NAME = "flagged-turn.log"
BREAKER_MAX = 3
BREAKER_WINDOW = 3600
RESTART_DELAY = "5s"

SAFEGUARD_NOTICE = re.compile(
    r"safeguards flagged|can't respond to (this|your last) message|can't help with this\. Start a new session"
)


def cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
    except OSError:
        return []


def parent(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return int(f.read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return 0


def is_bot_main_claude():
    """The nearest claude ancestor must be interactive and spawned by the
    service's expect helper."""
    pid = os.getppid()
    for _ in range(8):
        if pid <= 1:
            return False
        argv = cmdline(pid)
        if argv and os.path.basename(argv[0]) == "claude":
            if "-p" in argv or "--print" in argv:
                return False
            return any("claude-discord-spawn.expect" in a for a in cmdline(parent(pid)))
        pid = parent(pid)
    return False


def own_unit():
    try:
        with open("/proc/self/cgroup") as f:
            for line in f:
                m = re.search(r"/(claude-discord@[A-Za-z0-9._-]+\.service)(/|$)", line.strip())
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def log(config_dir, **fields):
    fields = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "from": "hook", **fields}
    try:
        with open(os.path.join(config_dir, LOG_NAME), "a") as f:
            f.write(json.dumps(fields) + "\n")
    except OSError:
        pass


def breaker_open(config_dir, now):
    path = os.path.join(config_dir, STATE_NAME)
    try:
        with open(path) as f:
            recent = [t for t in json.load(f) if now - t < BREAKER_WINDOW]
    except (OSError, ValueError):
        recent = []
    if len(recent) >= BREAKER_MAX:
        return True
    recent.append(now)
    with open(path, "w") as f:
        json.dump(recent, f)
    return False


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if event.get("hook_event_name") != "StopFailure" or event.get("error") != "invalid_request":
        return 0
    if not SAFEGUARD_NOTICE.search(event.get("last_assistant_message") or ""):
        return 0

    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    unit = own_unit()
    if unit is None or not is_bot_main_claude():
        log(config_dir, status="ignored", reason="not the bot's main claude", unit=unit)
        return 0
    transcript = event.get("transcript_path")
    if not transcript:
        log(config_dir, status="ignored", reason="no transcript_path")
        return 0
    now = int(time.time())
    if breaker_open(config_dir, now):
        log(config_dir, status="breaker", reason=f"{BREAKER_MAX} recoveries within {BREAKER_WINDOW}s; leaving it for a human")
        return 0

    marker = os.path.join(config_dir, MARKER_NAME)
    tmp = marker + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"transcript_path": transcript, "session_id": event.get("session_id"),
                   "ts": now}, f)
    os.replace(tmp, marker)

    restart = subprocess.run(
        ["systemd-run", "--user", "--collect", "--quiet", f"--on-active={RESTART_DELAY}",
         f"--unit=flagged-turn-restart-{now}",
         "systemctl", "--user", "restart", unit],
        capture_output=True, text=True)
    if restart.returncode != 0:
        os.unlink(marker)
        log(config_dir, status="error", reason="systemd-run failed", stderr=restart.stderr.strip())
        return 0
    log(config_dir, status="restarting", unit=unit, transcript=transcript)
    time.sleep(110)
    return 0


if __name__ == "__main__":
    sys.exit(main())
