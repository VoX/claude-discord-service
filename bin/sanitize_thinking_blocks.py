#!/usr/bin/env python3
"""
Sanitize orphaned thinking/redacted_thinking blocks from a Claude Code
session transcript JSONL. Targets the github.com/anthropics/claude-code/issues/63147
'thinking blocks cannot be modified' 400 that bricks --resume after a
model/login switch or a parallel-tool cancel.

What it does (per ductai199x/eslerm workaround):
- For each ASSISTANT message in the transcript, walk `message.content` and
  - drop `thinking` and `redacted_thinking` blocks from the array
  - if that empties the array, insert a single placeholder
    {"type":"text","text":"[thinking]"} so the row still has content
    (some downstream code asserts content is non-empty)
- Leave parentUuid / uuid / message_id / threading untouched
- Leave non-assistant rows untouched entirely
- Atomic write: write to `<path>.sanitized.tmp`, fsync, then rename over original

Backup is always created at `<path>.bak-<unix-ts>` before the rename.

Usage:
    python3 sanitize_thinking_blocks.py <transcript.jsonl>

Exits non-zero if no changes were needed (caller can decide whether to proceed).
"""
import json
import os
import sys
import time
from pathlib import Path

def sanitize_row(row: dict) -> tuple[bool, dict]:
    """Return (modified?, row). Mutates a copy."""
    msg = row.get("message")
    if not isinstance(msg, dict):
        return False, row
    if msg.get("role") != "assistant":
        return False, row
    content = msg.get("content")
    if not isinstance(content, list):
        return False, row
    filtered = [
        c for c in content
        if not (isinstance(c, dict) and c.get("type") in ("thinking", "redacted_thinking"))
    ]
    if len(filtered) == len(content):
        return False, row
    if not filtered:
        filtered = [{"type": "text", "text": "[thinking]"}]
    # Don't mutate the input in place — preserves the caller's view.
    new_msg = dict(msg)
    new_msg["content"] = filtered
    new_row = dict(row)
    new_row["message"] = new_msg
    return True, new_row

def main(path_str: str) -> int:
    path = Path(path_str)
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 2
    if not path.is_file():
        print(f"ERROR: {path} is not a regular file", file=sys.stderr)
        return 2

    total_rows = 0
    modified_rows = 0
    blocks_stripped = 0
    out_lines = []

    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                out_lines.append(line)
                continue
            total_rows += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                # Pass through unparseable lines unchanged — better than guessing.
                print(f"WARN: line {lineno} unparseable, keeping as-is: {e}", file=sys.stderr)
                out_lines.append(line)
                continue
            # Count thinking blocks BEFORE filter so we can report.
            content = (row.get("message") or {}).get("content")
            if isinstance(content, list):
                blocks_stripped += sum(
                    1 for c in content
                    if isinstance(c, dict) and c.get("type") in ("thinking", "redacted_thinking")
                )
            changed, new_row = sanitize_row(row)
            if changed:
                modified_rows += 1
                # ensure_ascii=False to preserve any unicode the original had.
                # separators omits trailing whitespace (matches Claude Code's writer).
                out_lines.append(json.dumps(new_row, ensure_ascii=False, separators=(",", ":")) + "\n")
            else:
                out_lines.append(line)

    if modified_rows == 0:
        print(f"{path}: {total_rows} rows, 0 thinking-block rows — nothing to do", file=sys.stderr)
        return 1

    # Backup, then atomic rename.
    ts = int(time.time())
    backup = path.with_suffix(path.suffix + f".bak-{ts}")
    tmp = path.with_suffix(path.suffix + ".sanitized.tmp")
    # Read original bytes for backup (preserves exact file content for rollback).
    backup.write_bytes(path.read_bytes())
    print(f"backup: {backup}", file=sys.stderr)

    with tmp.open("w", encoding="utf-8") as f:
        f.writelines(out_lines)
        f.flush()
        os.fsync(f.fileno())
    # Preserve owner/mode of original on rename.
    st = path.stat()
    os.chmod(tmp, st.st_mode)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.rename(tmp, path)

    print(
        f"{path}: {total_rows} rows scanned, "
        f"{modified_rows} assistant rows rewritten, "
        f"{blocks_stripped} thinking/redacted_thinking blocks removed",
        file=sys.stderr,
    )
    return 0

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: sanitize_thinking_blocks.py <transcript.jsonl>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
