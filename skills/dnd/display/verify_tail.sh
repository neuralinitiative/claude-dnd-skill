#!/usr/bin/env bash
# verify_tail.sh — confirm the campaign-side session_tail.json is healthy.
#
# Usage:
#   bash verify_tail.sh <campaign-name>
#
# Exit 0 = healthy (file exists, > 50 bytes, parses as a non-empty JSON list)
# Exit 1 = unhealthy (missing, empty/[], unparseable, or wrong shape)
#
# Used by SKILL-commands.md /dnd save and /dnd end to detect the wipe-bug
# state before it's too late to recover. When this returns 1, the DM is
# instructed to write a canonical tail directly to disk so the next
# /dnd load has working replay material.

set -u

CAMP="${1:-}"
if [[ -z "$CAMP" ]]; then
  echo "verify_tail.sh: usage: $0 <campaign-name>" >&2
  exit 2
fi

# Resolve the campaign root the same way the Python side does.
# DND_CAMPAIGN_ROOT env var > default ~/.claude/dnd/campaigns
ROOT="${DND_CAMPAIGN_ROOT:-$HOME/.claude/dnd/campaigns}"
TAIL="$ROOT/$CAMP/session_tail.json"

if [[ ! -f "$TAIL" ]]; then
  echo "verify_tail.sh: $TAIL — MISSING"
  exit 1
fi

SIZE=$(stat -f%z "$TAIL" 2>/dev/null || stat -c%s "$TAIL" 2>/dev/null || wc -c < "$TAIL")
if (( SIZE < 50 )); then
  echo "verify_tail.sh: $TAIL — TOO SMALL ($SIZE bytes; likely [] or near-empty)"
  exit 1
fi

# Parse + shape check via Python (already a dependency of the skill).
python3 - "$TAIL" <<'PY'
import json, sys
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
except Exception as e:
    print(f"verify_tail.sh: {path} — UNPARSEABLE: {e}")
    sys.exit(1)
if not isinstance(data, list):
    print(f"verify_tail.sh: {path} — NOT A LIST")
    sys.exit(1)
if not data:
    print(f"verify_tail.sh: {path} — EMPTY LIST")
    sys.exit(1)
if not all(isinstance(e, dict) and ("text" in e or "player" in e or "npc" in e or "dice" in e) for e in data):
    print(f"verify_tail.sh: {path} — entries not in expected shape")
    sys.exit(1)
print(f"verify_tail.sh: {path} — HEALTHY ({len(data)} entries)")
sys.exit(0)
PY
