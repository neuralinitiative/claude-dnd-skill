#!/usr/bin/env python3
"""write_canonical_tail.py — write a canonical session_tail.json from CLI input.

Used by SKILL-commands.md `/dnd end` step 1f as the backstop when
verify_tail.sh reports the on-disk tail is unhealthy. Writes directly to the
campaign-specific path with proper atomic semantics, so next /dnd load has
working replay material even if the display's own persistence path failed.

Usage:
    write_canonical_tail.py --campaign <name> --json '[
        {"text": "...", "_camp": "<name>"},
        {"player": "Mara", "text": "Mara draws her dagger.", "_camp": "<name>"},
        ...
    ]'

Or from a file:
    write_canonical_tail.py --campaign <name> --file /path/to/entries.json

The script enforces:
  - The campaign-specific path (no legacy fallback).
  - Atomic write (temp file + rename) so partial writes can't corrupt.
  - Every entry stamped with `_camp` (auto-stamped if missing).
  - Capped at 30 entries (the display's deque maxlen).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _resolve_campaign_root() -> Path:
    """Mirror paths.py logic without importing the skill (this script is
    self-contained so the SKILL-commands.md backstop can run even when
    /dnd is dead)."""
    env = os.environ.get("DND_CAMPAIGN_ROOT")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude" / "dnd" / "campaigns"


def main() -> int:
    p = argparse.ArgumentParser(description="Write a canonical session_tail.json (atomic, validated).")
    p.add_argument("--campaign", required=True, help="Campaign name (directory under campaigns/)")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--json", help="JSON list of entries as a string")
    src.add_argument("--file", help="Path to a file containing the JSON list")
    args = p.parse_args()

    raw = args.json if args.json is not None else open(args.file).read()
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"write_canonical_tail: input is not valid JSON: {e}", file=sys.stderr)
        return 2

    if not isinstance(entries, list) or not entries:
        print("write_canonical_tail: input must be a non-empty JSON list", file=sys.stderr)
        return 2

    # Auto-stamp _camp on every entry, validate shape minimally.
    cleaned: list = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            print(f"write_canonical_tail: entry #{i} is not an object", file=sys.stderr)
            return 2
        if not any(k in e for k in ("text", "player", "npc", "dice", "tutor", "action")):
            print(f"write_canonical_tail: entry #{i} has no recognizable content key", file=sys.stderr)
            return 2
        e = dict(e)  # copy
        e["_camp"] = args.campaign
        cleaned.append(e)

    cleaned = cleaned[-30:]  # match the display deque's maxlen

    target_dir = _resolve_campaign_root() / args.campaign
    if not target_dir.exists():
        print(f"write_canonical_tail: campaign dir not found: {target_dir}", file=sys.stderr)
        return 1
    target = target_dir / "session_tail.json"

    # Atomic write: temp file + rename
    tmp = target.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(cleaned, f, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, target)

    print(f"write_canonical_tail: wrote {len(cleaned)} entries → {target} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
