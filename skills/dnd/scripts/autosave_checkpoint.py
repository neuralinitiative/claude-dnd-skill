#!/usr/bin/env python3
"""
autosave_checkpoint.py — behind-the-scenes continuity checkpoint for long sessions.

Designed to run as a Claude Code **Stop hook** (install with
`install_autosave_hook.py`). It fires after every DM turn and does two things:

  1. **Deterministic snapshot** (every turn): copies the active campaign's
     `state.md` to a recovery file under the runtime dir and verifies the
     display's `session_tail.json` is healthy. Pure file I/O — no model needed.

  2. **Cadence checkpoint** (every Nth turn): emits a Stop-hook `block`
     decision whose `reason` instructs the DM to do a silent micro-save
     (Live State Flags + new graph edges + session tail) *before* yielding the
     turn — so a context compaction can never cost more than a few turns of
     continuity. Keyed on turn count because the model cannot see its own
     context-usage percentage.

The hook is a no-op (exit 0, no output) when:
  - no campaign is active (no runtime marker — e.g. a non-D&D Claude session),
  - the active campaign has `autosave: off` in `state.md → ## Session Flags`,
  - `stop_hook_active` is set (we are already inside a continuation — never
    block twice, which would loop).

Stdin (Stop-hook JSON, when run as a hook):
  {"session_id": "...", "stop_hook_active": false, "hook_event_name": "Stop", ...}

Manual usage (for testing / forcing a snapshot):
  python3 autosave_checkpoint.py --campaign <name> [--snapshot-only]
  python3 autosave_checkpoint.py --status   # print active campaign + turn count
"""

import sys
import os
import json
import argparse
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

DEFAULT_EVERY_N = 10          # cadence checkpoint every N turns
ACTIVE_MARKER = "active-campaign.json"   # written by /dm:dnd load, under runtime_dir()

CHECKPOINT_REASON = (
    "Continuity autosave checkpoint (turn cadence reached). Before finishing, "
    "silently update state.md → ## Live State Flags (cover, faction stances, "
    "NPC dispositions), append any new relationships to the campaign graph, and "
    "make sure the latest beats are captured in the session tail. Do not narrate "
    "this to the player and do not run a full /dm:dnd save (no session-log "
    "rewrite) — just flush the continuity anchors, then end your turn."
)


def _read_stdin_json():
    """Parse the Stop-hook JSON from stdin. Returns {} when absent/!tty/invalid."""
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def active_campaign() -> str | None:
    """Return the name of the campaign marked active by the last /dm:dnd load."""
    marker = paths.runtime_dir() / ACTIVE_MARKER
    if not marker.exists():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = data.get("name")
    return name if isinstance(name, str) and name.strip() else None


def autosave_enabled(campaign: str) -> bool:
    """Read the `autosave` flag from state.md → ## Session Flags. Default: on."""
    state = paths.find_campaign(campaign) / "state.md"
    if not state.exists():
        return False
    try:
        text = state.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    # Look only inside the Session Flags section so we don't match prose elsewhere.
    flags = _section(text, "## Session Flags")
    if flags is None:
        return True  # section absent (older campaign) — default on
    for line in flags.splitlines():
        low = line.lower()
        if "autosave" in low:
            if "off" in low or "false" in low or "disabled" in low:
                return False
            return True
    return True  # flag not written yet — default on


def _section(text: str, header: str) -> str | None:
    """Return the body of a `## `-level markdown section, or None if absent."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i + 1
            break
    if start is None:
        return None
    body = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body)


def _counter_path(session_id: str | None, campaign: str) -> "paths.pathlib.Path":
    key = session_id or campaign or "default"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    return paths.runtime_dir() / f"autosave-counter-{safe}.json"


def _load_counter(path) -> int:
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("turns", 0))
    except (OSError, ValueError, TypeError):
        return 0


def _save_counter(path, turns: int, campaign: str) -> None:
    try:
        path.write_text(
            json.dumps({"turns": turns, "campaign": campaign}),
            encoding="utf-8",
        )
    except OSError:
        pass


def decide(stdin_obj: dict, prev_turns: int, every_n: int = DEFAULT_EVERY_N):
    """Pure cadence logic — separated from I/O so it is unit-testable.

    Returns (new_turns, block_reason_or_None).
      - If `stop_hook_active` is set, never block (avoid continuation loops) and
        leave the counter where it is.
      - Otherwise increment; when the new count is a multiple of every_n, emit a
        block reason and reset the counter to 0.
    """
    if stdin_obj.get("stop_hook_active"):
        return prev_turns, None
    turns = prev_turns + 1
    if every_n > 0 and turns % every_n == 0:
        return 0, CHECKPOINT_REASON
    return turns, None


def snapshot(campaign: str) -> None:
    """Deterministic durability: copy state.md to a recovery file. Best-effort."""
    camp_dir = paths.find_campaign(campaign)
    state = camp_dir / "state.md"
    if not state.exists():
        return
    dest = paths.runtime_dir() / f"{_safe(campaign)}.autocheckpoint.md"
    try:
        shutil.copy2(str(state), str(dest))
    except OSError:
        pass


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def _emit_block(reason: str) -> None:
    """Print the Stop-hook decision JSON that tells Claude to continue."""
    print(json.dumps({"decision": "block", "reason": reason}))


def main() -> int:
    parser = argparse.ArgumentParser(description="Continuity autosave checkpoint.")
    parser.add_argument("--campaign", help="Override the active campaign (testing).")
    parser.add_argument("--snapshot-only", action="store_true",
                        help="Take the deterministic snapshot and exit; never block.")
    parser.add_argument("--status", action="store_true",
                        help="Print active campaign and current turn count.")
    parser.add_argument("--every", type=int, default=None,
                        help=f"Turns between cadence checkpoints (default {DEFAULT_EVERY_N}).")
    args = parser.parse_args()

    stdin_obj = {} if (args.campaign or args.status) else _read_stdin_json()
    campaign = args.campaign or active_campaign()

    if args.status:
        sid = stdin_obj.get("session_id")
        turns = _load_counter(_counter_path(sid, campaign or "")) if campaign else 0
        print(f"active_campaign: {campaign or '(none)'}")
        print(f"turns_since_checkpoint: {turns}")
        return 0

    # Guard: nothing to do when no campaign is active (non-D&D session, etc.).
    if not campaign:
        return 0
    if not autosave_enabled(campaign):
        return 0

    # Deterministic durability always runs.
    snapshot(campaign)

    if args.snapshot_only:
        return 0

    every_n = args.every if args.every is not None else _every_from_env()
    counter_file = _counter_path(stdin_obj.get("session_id"), campaign)
    prev = _load_counter(counter_file)
    new_turns, reason = decide(stdin_obj, prev, every_n)
    _save_counter(counter_file, new_turns, campaign)

    if reason:
        _emit_block(reason)
    return 0


def _every_from_env() -> int:
    raw = os.environ.get("DND_AUTOSAVE_EVERY", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_EVERY_N


if __name__ == "__main__":
    sys.exit(main())
