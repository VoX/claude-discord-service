#!/usr/bin/env python3
"""
Rewind a bot's session past the turn(s) the model's safeguards refused.

Why: when a safeguard flags a turn, the flagged message stays in the
conversation, so every later turn re-sends it and gets flagged too. A
headless bot has nobody to press Esc-Esc, and /clear throws away the whole
session to get rid of one message. This drops just the refused turn(s).

How: it does what Claude Code's own /rewind does. Rewind does not delete
anything; it appends a `last-prompt` entry with an explicit `leafUuid` (plus
`rewound: true`), and on --resume the loader takes the conversation as the
parentUuid chain ending at that leaf. Entries after it stay in the file but
sit on an abandoned branch and are never loaded. So this script is
append-only: no line of the transcript is rewritten or removed, and undoing
it is appending another `last-prompt` that points back at the old leaf (the
log line records both).

What counts as a refused turn: turns end with a `system/turn_duration`
entry, and a refusal ends its turn, so the newest user/assistant entry of a
refused turn is the synthetic assistant API error with
`stop_reason: "refusal"`. The script walks back from the current leaf, drops
consecutive refused turns, and points the leaf at the newest entry of the
last turn that was NOT refused. It drops nothing unless the NEWEST turn is a
refusal, so a bot that already moved on is left alone.

It only drops. It never re-sends, rewords, or retries the refused message.

Usage:
    drop_flagged_turn.py <transcript.jsonl> [--dry-run]
    drop_flagged_turn.py --from-marker          (ExecStartPre)

--from-marker reads $CLAUDE_CONFIG_DIR/flagged-turn.request, written by the
on_stop_failure.py hook, and is a silent no-op (exit 0) when there is no marker. That
keeps the rewind tied to an observed refusal rather than to every restart.
It also queues a one-shot scheduler on_startup note so the bot knows which
inbound message it lost. The note carries only the message's ids, never its
text, since the text is the thing that got flagged.

Must run while the bot is stopped (ExecStartPre), like the sanitizer: the
wrapper flock means no live claude is appending to the transcript.

Exit codes: 0 rewound, 1 nothing to do, 2 usage error, 4 guard refused.
"""
import json
import os
import re
import secrets
import sys
import time

MARKER_NAME = "flagged-turn.request"
LOG_NAME = "flagged-turn.log"
MAX_DROPPED_TURNS = 5
MAX_DROPPED_ENTRIES = 1000
TAIL_START = 8 << 20

CHAIN_TYPES = ("user", "assistant", "system", "attachment", "progress")
CHANNEL_ATTRS = ("source", "chat_id", "message_id", "user", "user_id", "ts")


def log(msg):
    print(f"drop-flagged: {msg}", file=sys.stderr)


def is_chain(e):
    return isinstance(e, dict) and e.get("type") in CHAIN_TYPES and isinstance(e.get("uuid"), str)


def is_turn_end(e):
    return e.get("type") == "system" and e.get("subtype") == "turn_duration"


def is_message(e):
    return e.get("type") in ("user", "assistant")


def is_refusal(e):
    msg = e.get("message") or {}
    return (e.get("type") == "assistant" and e.get("isApiErrorMessage") is True
            and msg.get("stop_reason") == "refusal")


def read_tail(path, nbytes):
    """Parse the last nbytes of the file. Returns (entries, reached_start)."""
    size = os.path.getsize(path)
    start = max(0, size - nbytes)
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read()
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]  # first line is partial
    out = []
    for line in lines:
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out, start == 0


def find_leaf(entries):
    """Mirror the loader: an explicit last-prompt leaf wins until a newer
    transcript entry lands after it; otherwise the newest main-thread entry."""
    newest, explicit_leaf = None, None
    for e in entries:
        if is_chain(e):
            if not e.get("isSidechain"):
                newest = e["uuid"]
                explicit_leaf = None
        elif e.get("type") == "last-prompt" and e.get("leafUuid"):
            explicit_leaf = e["leafUuid"] if e.get("explicit") is True else None
    return explicit_leaf or newest


def channel_attrs(e):
    """Ids of an inbound channel message, from its opening tag only."""
    content = (e.get("message") or {}).get("content")
    if not isinstance(content, str) or not content.startswith("<channel "):
        return None
    tag = content.split(">", 1)[0]
    attrs = dict(re.findall(r'(\w+)="([^"]*)"', tag))
    picked = {k: attrs[k] for k in CHANNEL_ATTRS if k in attrs}
    return picked or None


class Plan:
    def __init__(self):
        self.old_leaf = None
        self.new_leaf = None
        self.session_id = None
        self.turns = 0
        self.entries = 0
        self.dropped_inbound = []


def analyze(entries, reached_start, force=False):
    """Returns (status, plan_or_reason). status: 'cut' | 'noop' | 'refuse' | 'more'."""
    by_uuid = {e["uuid"]: e for e in entries if is_chain(e)}
    leaf = find_leaf(entries)
    if leaf is None:
        return "noop", "no conversation entries"

    plan = Plan()
    plan.old_leaf = leaf
    # Walk newest -> oldest. `head` is the newest user/assistant entry of the
    # turn being walked; a turn_duration seen after entries of a turn closes
    # that turn and opens the next-older one.
    head = None
    turn_len = 0
    cur = leaf
    seen = set()
    while True:
        e = by_uuid.get(cur) if cur else None
        if e is None:
            if cur and not reached_start:
                return "more", None
            return "refuse", "reached the start of the chain without finding an unrefused turn"
        if cur in seen:
            return "refuse", "parentUuid cycle"
        seen.add(cur)

        if is_turn_end(e) and turn_len:
            if head is None:
                return "refuse", f"turn ending before {e['uuid']} has no user/assistant entry"
            plan.turns += 1
            plan.entries += turn_len
            head, turn_len = None, 0
            if not force:
                if plan.turns > MAX_DROPPED_TURNS:
                    return "refuse", f"more than {MAX_DROPPED_TURNS} refused turns in a row"
                if plan.entries > MAX_DROPPED_ENTRIES:
                    return "refuse", f"refused turns span more than {MAX_DROPPED_ENTRIES} entries"
        turn_len += 1

        if head is None and is_message(e):
            head = e
            if not is_refusal(head):
                if plan.turns == 0:
                    return "noop", "newest turn was not refused"
                plan.new_leaf = head["uuid"]
                plan.session_id = head.get("sessionId")
                return "cut", plan
        elif e.get("type") == "user":
            attrs = channel_attrs(e)
            if attrs:
                plan.dropped_inbound.append(attrs)
        cur = e.get("parentUuid") or e.get("logicalParentUuid")


def plan_for(path, force=False):
    nbytes = TAIL_START
    while True:
        entries, reached_start = read_tail(path, nbytes)
        status, result = analyze(entries, reached_start, force=force)
        if status != "more":
            return status, result
        nbytes *= 4


def append_rewind(path, plan):
    entry = {"type": "last-prompt", "leafUuid": plan.new_leaf, "explicit": True,
             "rewound": True}
    if plan.session_id:
        entry["sessionId"] = plan.session_id
    with open(path, "rb+") as f:
        f.seek(0, os.SEEK_END)
        if f.tell() > 0:
            f.seek(-1, os.SEEK_END)
            needs_newline = f.read(1) != b"\n"
        else:
            needs_newline = False
        f.seek(0, os.SEEK_END)
        # Compact separators are load-bearing: Claude Code's transcript loader
        # finds last-prompt entries by matching the raw bytes
        # `{"type":"last-prompt"`, so json.dumps' default `": "` spacing makes
        # the rewind invisible to --resume and the bot resumes the refused leaf.
        f.write((b"\n" if needs_newline else b"") + json.dumps(entry, separators=(",", ":")).encode() + b"\n")
        f.flush()
        os.fsync(f.fileno())


def queue_startup_note(config_dir, plan):
    jobs_file = os.path.join(config_dir, "channels", "scheduler", "jobs.json")
    if not os.path.isdir(os.path.dirname(jobs_file)):
        return
    try:
        with open(jobs_file) as f:
            store = json.load(f)
    except FileNotFoundError:
        store = {"jobs": []}
    except ValueError as err:
        log(f"jobs.json unreadable, skipping startup note: {err}")
        return
    if plan.dropped_inbound:
        lost = "; ".join(" ".join(f'{k}={v}' for k, v in a.items()) for a in plan.dropped_inbound)
    else:
        lost = "(no inbound channel message identified; the trigger may have been a tool result)"
    text = (
        f"Safeguard recovery: before this restart the model safeguards refused {plan.turns} turn(s), "
        "and the service rewound your session to the last turn before them, so that content is no "
        f"longer in your context. Inbound message(s) dropped: {lost}. "
        "Don't ask for it to be resent and don't try to reconstruct it. If someone is waiting on a "
        "reply, a one-line note that their message tripped the safeguard and was dropped is enough; "
        "otherwise carry on."
    )
    store.setdefault("jobs", []).append({
        "id": "sched_" + secrets.token_hex(6),
        "fire_at": 0,
        "text": text,
        "execution_count": 0,
        "created_at": int(time.time() * 1000),
        "title": "safeguard recovery",
        "on_startup": True,
    })
    tmp = jobs_file + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(store, f, indent=2)
    os.replace(tmp, jobs_file)


def record(config_dir, path, status, detail):
    line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "transcript": path,
            "status": status, **detail}
    try:
        with open(os.path.join(config_dir, LOG_NAME), "a") as f:
            f.write(json.dumps(line) + "\n")
    except OSError as err:
        log(f"could not write {LOG_NAME}: {err}")


def run(path, config_dir, dry_run=False, notify=False, force=False):
    status, result = plan_for(path, force=force)
    if status != "cut":
        log(f"{status}: {result}")
        if config_dir:
            record(config_dir, path, status, {"reason": result})
        return 1 if status == "noop" else 4
    plan = result
    detail = {"old_leaf": plan.old_leaf, "new_leaf": plan.new_leaf, "turns": plan.turns,
              "entries": plan.entries, "dropped_inbound": plan.dropped_inbound,
              "undo": f"append a last-prompt with leafUuid={plan.old_leaf} explicit=true"}
    if dry_run:
        print(json.dumps(detail, indent=1))
        return 0
    append_rewind(path, plan)
    log(f"rewound {plan.turns} refused turn(s): leaf {plan.old_leaf} -> {plan.new_leaf}")
    if config_dir:
        record(config_dir, path, "rewound", detail)
        if notify:
            queue_startup_note(config_dir, plan)
    return 0


def from_marker():
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    marker = os.path.join(config_dir, MARKER_NAME)
    try:
        with open(marker) as f:
            request = json.load(f)
    except FileNotFoundError:
        # The normal case on every restart; exit 0 so `systemctl status`
        # doesn't show the ExecStartPre as FAILURE on each boot.
        return 0
    except ValueError as err:
        log(f"bad marker, removing: {err}")
        os.unlink(marker)
        return 2
    # Consume the marker first: a crash below must not turn into a rewind on
    # every later restart.
    os.unlink(marker)
    path = os.path.realpath(request.get("transcript_path") or "")
    projects = os.path.realpath(os.path.join(config_dir, "projects")) + os.sep
    if not (path.startswith(projects) and path.endswith(".jsonl") and os.path.isfile(path)):
        log(f"marker transcript_path outside {projects} or missing: {path!r}")
        return 2
    return run(path, config_dir, notify=True)


def main(argv):
    if argv[1:] == ["--from-marker"]:
        return from_marker()
    dry_run = "--dry-run" in argv
    force = "--force" in argv
    notify = "--no-notify" not in argv
    args = [a for a in argv[1:] if a not in ("--dry-run", "--force", "--no-notify")]
    if len(args) != 1 or not os.path.isfile(args[0]):
        print(__doc__, file=sys.stderr)
        return 2
    path = os.path.realpath(args[0])
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if not config_dir:
        m = re.search(r"(.*?/\.claude)(?:/|$)", path)
        if m:
            config_dir = m.group(1)
    return run(path, config_dir, dry_run=dry_run, force=force, notify=notify)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
