#!/usr/bin/env python3
"""
Sanitize orphaned thinking/redacted_thinking blocks from a Claude Code
session transcript JSONL. Targets the github.com/anthropics/claude-code/issues/63147
'thinking blocks cannot be modified' 400 that bricks --resume after a
mid-turn interruption or model switch.

NOTE — long-lived workaround. This is a model-side / API-side regression
that hits both Opus 4.7 and 4.8 (per VoX's read of upstream issue
reports). It is NOT a Claude Code version bug, so pinning a different
CC release does not help. Keep this sanitizer (and the ExecStartPre hook
that calls it) until the model-side behavior actually changes. The next
person who sees a `closed` upstream issue and an active sanitize hook
should resist the urge to rip it out — verify the wedge is actually
fixed by attempting a resume against a transcript with a deliberately-
orphaned trailing thinking block first.

What it does (per ductai199x/eslerm workaround):
- For each ASSISTANT message in the transcript, walk `message.content` and
  - drop `thinking` and `redacted_thinking` blocks from the array
  - if that empties the array, insert a single placeholder
    {"type":"text","text":"[thinking]"} so the row still has content
    (some downstream code asserts content is non-empty)
- Leave parentUuid / uuid / message_id / threading untouched
- Leave non-assistant rows untouched entirely
- Streaming write: open `<path>.sanitized.tmp` for write, iterate the input
  one line at a time, fsync, then rename — bounded memory regardless of
  transcript size (some bots have multi-hundred-MB .bak files in the wild).

Concurrent-writer note: this script does NOT flock the target. The
wrapper.lock at the service layer prevents two claude --resume's against
the same session from running concurrently, so if you invoke this from
the ExecStartPre hook (which fires before claude starts), there is no
live writer to race. Manual invocation while a bot is actively writing
to the transcript will lose any concurrent appends — don't do that.

Backup is always created at `<path>.bak-<unix-ts>` before the rename
(also streamed). After write, prune older .bak-* keeping the N most
recent (default 5; set SANITIZE_KEEP_BACKUPS to override).

Usage:
    python3 sanitize_thinking_blocks.py <transcript.jsonl>

Exit codes:
    0 — sanitize applied, backup created
    1 — nothing to do (no thinking blocks found); no files written
    2 — usage error (bad/missing path)
    3 — write/permission failure mid-operation; backup may exist
"""
import json
import os
import sys
import time
from pathlib import Path

KEEP_BACKUPS = int(os.environ.get("SANITIZE_KEEP_BACKUPS", "5"))


def sanitize_row(row: dict) -> tuple[bool, dict, int]:
    """Return (modified?, row, blocks_removed). Returns a copy of the row
    when modified — leaves the input dict untouched so the caller can pass
    through the original line unchanged."""
    msg = row.get("message")
    if not isinstance(msg, dict):
        return False, row, 0
    if msg.get("role") != "assistant":
        return False, row, 0
    content = msg.get("content")
    if not isinstance(content, list):
        return False, row, 0
    blocks_removed = sum(
        1 for c in content
        if isinstance(c, dict) and c.get("type") in ("thinking", "redacted_thinking")
    )
    if blocks_removed == 0:
        return False, row, 0
    filtered = [
        c for c in content
        if not (isinstance(c, dict) and c.get("type") in ("thinking", "redacted_thinking"))
    ]
    if not filtered:
        filtered = [{"type": "text", "text": "[thinking]"}]
    new_msg = dict(msg)
    new_msg["content"] = filtered
    new_row = dict(row)
    new_row["message"] = new_msg
    return True, new_row, blocks_removed


def prune_backups(transcript_path: Path, keep: int) -> int:
    """Remove .bak-<ts> siblings beyond the N most recent. Returns count pruned."""
    prefix = transcript_path.name + ".bak-"
    backups = sorted(
        (p for p in transcript_path.parent.iterdir()
         if p.is_file() and p.name.startswith(prefix)),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    pruned = 0
    for old in backups[keep:]:
        try:
            old.unlink()
            pruned += 1
        except OSError as e:
            print(f"WARN: failed to prune {old.name}: {e}", file=sys.stderr)
    return pruned


def stream_backup(src: Path, dst: Path) -> None:
    """Copy src → dst byte-for-byte in 1MB chunks. Avoids loading the whole
    file into memory the way `dst.write_bytes(src.read_bytes())` would."""
    with src.open("rb") as r, dst.open("wb") as w:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            w.write(chunk)
        w.flush()
        os.fsync(w.fileno())


def main(path_str: str) -> int:
    path = Path(path_str)
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 2
    if not path.is_file():
        print(f"ERROR: {path} is not a regular file", file=sys.stderr)
        return 2

    # First pass: count blocks without loading the file into memory. Lets us
    # exit 1 fast (no backup, no tmp write) when there's nothing to do.
    total_rows = 0
    blocks_total = 0
    modified_rows_total = 0
    partial_last_line = False
    last_line_was_lf = True
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                last_line_was_lf = line.endswith("\n")
                continue
            total_rows += 1
            last_line_was_lf = line.endswith("\n")
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # Could be a partial last line (writer interrupted) or a
                # genuinely corrupt mid-file row. Skip counting; we'll
                # pass it through as-is in the rewrite pass.
                continue
            content = (row.get("message") or {}).get("content")
            if isinstance(content, list):
                row_blocks = sum(
                    1 for x in content
                    if isinstance(x, dict) and x.get("type") in ("thinking", "redacted_thinking")
                )
                if row_blocks:
                    blocks_total += row_blocks
                    modified_rows_total += 1
    if not last_line_was_lf and total_rows > 0:
        partial_last_line = True
        print(
            f"WARN: {path.name} last line lacks a trailing newline — claude was "
            "likely killed mid-write. Passing through as-is, but this may indicate "
            "transcript corruption the sanitizer can't fix.",
            file=sys.stderr,
        )

    if modified_rows_total == 0:
        print(f"{path.name}: {total_rows} rows, 0 thinking-block rows — nothing to do", file=sys.stderr)
        return 1

    # Second pass: streaming rewrite to .sanitized.tmp, then atomic rename.
    ts = int(time.time())
    backup = path.with_suffix(path.suffix + f".bak-{ts}")
    tmp = path.with_suffix(path.suffix + ".sanitized.tmp")

    try:
        # Backup first so a mid-rewrite crash leaves the original untouched
        # (we never modify `path` itself until the final atomic rename).
        stream_backup(path, backup)
        print(f"backup: {backup}", file=sys.stderr)

        # Streaming rewrite. ensure_ascii=False + tight separators to match
        # Claude Code's writer style (avoids gratuitous \uXXXX escapes that
        # would make the modified rows visually diverge from the unmodified
        # passthrough rows on grep/diff).
        with path.open("r", encoding="utf-8") as r, tmp.open("w", encoding="utf-8") as w:
            for line in r:
                if not line.strip():
                    w.write(line)
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    w.write(line)
                    continue
                changed, new_row, _ = sanitize_row(row)
                if changed:
                    w.write(json.dumps(new_row, ensure_ascii=False, separators=(",", ":")) + "\n")
                else:
                    w.write(line)
            w.flush()
            os.fsync(w.fileno())

        # Preserve mode and (best-effort) ownership of the original. chown
        # commonly fails for non-root users trying to change uid/gid even to
        # the same values when CAP_CHOWN is missing — swallow the error and
        # leave the tmp file with the runtime user's ownership. The atomic
        # rename still gives the same path, mode is preserved, only ownership
        # may differ. That's better than aborting and leaving the original
        # un-sanitized.
        st = path.stat()
        try:
            os.chmod(tmp, st.st_mode)
        except OSError as e:
            print(f"WARN: chmod({tmp.name}) failed: {e}", file=sys.stderr)
        if os.geteuid() != st.st_uid or os.getegid() != st.st_gid:
            try:
                os.chown(tmp, st.st_uid, st.st_gid)
            except OSError as e:
                print(f"WARN: chown({tmp.name}) failed (likely missing CAP_CHOWN): {e}", file=sys.stderr)

        os.rename(tmp, path)
    except OSError as e:
        print(f"ERROR: sanitize failed mid-operation: {e}", file=sys.stderr)
        # Clean up the tmp file if it exists; the backup (if written) stays
        # so it can be inspected / rolled back manually.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return 3

    # Prune older backups (best-effort, never fatal).
    pruned = prune_backups(path, KEEP_BACKUPS)
    pruned_note = f", pruned {pruned} older backup(s)" if pruned else ""

    print(
        f"{path.name}: {total_rows} rows scanned, "
        f"{modified_rows_total} assistant rows rewritten, "
        f"{blocks_total} thinking/redacted_thinking blocks removed"
        f"{pruned_note}"
        + (" (note: partial last line passed through)" if partial_last_line else ""),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: sanitize_thinking_blocks.py <transcript.jsonl>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
