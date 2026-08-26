#!/usr/bin/env python3
"""migrate_v1_to_v2.py — one-time helper to move a legacy v1 *standalone* install
(`~/.claude/skills/dnd`, invoked as `/dnd`) over to the v2 *plugin* install
(`dm@neural-initiative`, invoked as `/dm:dnd`).

Why this is light, not scary
----------------------------
Your campaign data was never inside the skill. It lives at the DATA root
(`~/.claude/dnd`, or `$DND_CAMPAIGN_ROOT`) and is read identically by both the
standalone skill and the plugin — see scripts/paths.py. So characters,
campaigns, and history need *zero* migration; both versions already share them.

This helper only does the small, fiddly bits:

  1. Detects a legacy standalone install and reports its version.
  2. Carries over **runtime state** that older standalone versions wrote *inside*
     the skill (`~/.claude/skills/dnd/display/`) — device approvals, the auth
     token, and TLS certs — into the update-safe runtime dir
     (`<data-root>/.runtime`). This is the real payoff: phones stay paired and
     HTTPS stays trusted, so nobody has to re-approve every device.
  3. Verifies (does NOT move) your campaign data at the DATA root.
  4. Backs up and retires the old `~/.claude/skills/dnd` so the legacy `/dnd`
     command no longer shadows or duplicates the new `/dm:dnd`.

It does NOT (and cannot) run `/plugin install` for you — that is a Claude Code
UI command. Install the plugin first, then run this. The two installs coexist
harmlessly in between (same data root), so there is no danger window.

Usage
-----
    python3 migrate_v1_to_v2.py            # interactive (asks before retiring v1)
    python3 migrate_v1_to_v2.py --yes      # non-interactive (auto-confirm)
    python3 migrate_v1_to_v2.py --dry-run  # show what would happen, change nothing
    python3 migrate_v1_to_v2.py --keep-standalone   # relocate runtime only; leave v1 in place

Exit codes: 0 success / nothing to do · 1 user declined · 2 error.
"""

import argparse
import os
import pathlib
import shutil
import sys
import time

# Resolve the new runtime location via the canonical resolver when available
# (this script ships next to paths.py in the plugin, so the import normally
# succeeds and runtime_dir() points at <data-root>/.runtime). Fall back to an
# identical computation if run detached from the package.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from paths import runtime_dir as _runtime_dir, _root as _data_root  # type: ignore
except Exception:  # pragma: no cover - defensive fallback
    def _data_root() -> pathlib.Path:
        raw = os.environ.get("DND_CAMPAIGN_ROOT", "").strip()
        return pathlib.Path(raw).expanduser().resolve() if raw else pathlib.Path("~/.claude/dnd").expanduser()

    def _runtime_dir() -> pathlib.Path:
        raw = os.environ.get("DND_RUNTIME_DIR", "").strip()
        d = pathlib.Path(raw).expanduser() if raw else (_data_root() / ".runtime")
        d.mkdir(parents=True, exist_ok=True)
        return d


# Legacy standalone location. Defaults to the canonical ~/.claude/skills/dnd;
# override with DND_LEGACY_SKILL_DIR for a non-default install (or for testing).
LEGACY_SKILL = pathlib.Path(
    os.environ.get("DND_LEGACY_SKILL_DIR", "").strip() or "~/.claude/skills/dnd"
).expanduser()

# Runtime-state files older standalone builds wrote into <skill>/display/.
# These are the writable session/auth/approval/cert files — NOT the process
# management files (.scheme, app.log, app.pid, .cert-server.pid), which are
# recreated on every launch and intentionally left behind.
RUNTIME_FILES = [
    ".approved_devices.json",  # paired LAN devices — the prize: no re-pairing
    ".pending_devices.json",
    ".token",                  # display auth token
    "cert.pem",                # TLS cert — keep so phones don't re-prompt trust
    "key.pem",
    ".campaign",               # active-campaign pointer
    ".help-lock",
    ".input_queue",
    ".input_trigger",
    "stats.json",
    "text_log.json",
    "player_input.json",
    "input_log.json",
]
# The subset whose loss is actually felt by players (surfaced in the summary).
VALUABLE = {".approved_devices.json", ".token", "cert.pem", "key.pem"}


def _say(msg: str = "") -> None:
    print(msg)


def _read_legacy_version() -> str:
    vf = LEGACY_SKILL / "VERSION"
    try:
        return vf.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def _relocate_runtime(dry_run: bool) -> list:
    """Copy runtime files from the legacy display dir into the new runtime dir.

    Never clobbers a file that already exists at the destination (a newer
    install's state wins). Returns the list of (name, note) actually carried.
    """
    src_dir = LEGACY_SKILL / "display"
    dest_dir = _runtime_dir()
    carried = []
    if not src_dir.exists():
        return carried
    for name in RUNTIME_FILES:
        src = src_dir / name
        if not src.exists():
            continue
        dest = dest_dir / name
        if dest.exists():
            carried.append((name, "skipped — already present in new runtime dir"))
            continue
        tag = " (device pairings)" if name == ".approved_devices.json" else (
            " (TLS cert)" if name in ("cert.pem", "key.pem") else "")
        if dry_run:
            carried.append((name, f"would copy{tag}"))
            continue
        try:
            shutil.copy2(str(src), str(dest))
            carried.append((name, f"carried over{tag}"))
        except OSError as e:
            carried.append((name, f"FAILED: {e}"))
    return carried


def _verify_campaign_data() -> tuple:
    root = _data_root()
    camps = root / "campaigns"
    chars = root / "characters"
    n_camp = len([p for p in camps.iterdir() if p.is_dir()]) if camps.exists() else 0
    n_char = len(list(chars.glob("*.md"))) if chars.exists() else 0
    return root, n_camp, n_char


def _retire_standalone(yes: bool, dry_run: bool) -> int:
    """Back up and remove the legacy standalone skill dir. Returns 0/1/2."""
    if LEGACY_SKILL.is_symlink():
        _say("• The legacy skill is a SYMLINK (likely a dev clone or GNU Stow setup).")
        _say(f"  Leaving it untouched. Remove the link yourself when ready:")
        _say(f"      rm '{LEGACY_SKILL}'")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = LEGACY_SKILL.with_name(f"dnd.v1-backup-{stamp}")
    _say()
    _say(f"• Retire the old standalone skill so /dnd no longer shadows /dm:dnd:")
    _say(f"      {LEGACY_SKILL}")
    _say(f"  It will be moved to a backup (not deleted):")
    _say(f"      {backup}")
    if dry_run:
        _say("  [dry-run] no changes made.")
        return 0
    if not yes:
        try:
            ans = input("  Proceed? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            _say("  Skipped. (Re-run with --yes to auto-confirm, or remove it manually later.)")
            return 1
    try:
        shutil.move(str(LEGACY_SKILL), str(backup))
        _say(f"  ✓ Moved to {backup}")
    except OSError as e:
        _say(f"  ✗ Could not move it: {e}", )
        return 2
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Migrate a v1 standalone D&D skill install to the v2 plugin.")
    ap.add_argument("--yes", action="store_true", help="auto-confirm destructive steps")
    ap.add_argument("--dry-run", action="store_true", help="show actions, change nothing")
    ap.add_argument("--keep-standalone", action="store_true",
                    help="relocate runtime state but leave the old skill in place")
    args = ap.parse_args()

    _say("D&D skill — v1 (standalone) → v2 (plugin) migration")
    _say("=" * 52)

    if not LEGACY_SKILL.exists():
        _say(f"No standalone install found at {LEGACY_SKILL}.")
        _say("Nothing to migrate — you're either already on the plugin or starting fresh.")
        _say("Install the plugin with:")
        _say("    /plugin marketplace add neuralinitiative/claude-dnd-skill")
        _say("    /plugin install dm@neural-initiative")
        return 0

    _say(f"• Found a standalone install (version {_read_legacy_version()}) at:")
    _say(f"      {LEGACY_SKILL}")

    # 1) Carry over runtime state (device pairings, token, TLS certs, session).
    _say()
    _say(f"• Relocating runtime state → {_runtime_dir()}")
    carried = _relocate_runtime(args.dry_run)
    if not carried:
        _say("  (nothing to carry — older runtime files not present; "
             "a 1.13.x standalone already kept them in the shared runtime dir)")
    else:
        for name, note in carried:
            _say(f"    - {name}: {note}")
        kept = [n for n, note in carried if "carried" in note or "would copy" in note]
        if any(n in VALUABLE for n in kept):
            _say("  → Device pairings / TLS trust preserved; phones won't need re-approval.")

    # 2) Reassure about campaign data (shared root — never moved).
    root, n_camp, n_char = _verify_campaign_data()
    _say()
    _say(f"• Campaign data lives at the shared DATA root and is untouched:")
    _say(f"      {root}   ({n_camp} campaign(s), {n_char} character(s))")

    # 3) Retire the old standalone (unless asked to keep it).
    rc = 0
    if args.keep_standalone:
        _say()
        _say("• Leaving the standalone install in place (--keep-standalone).")
        _say("  Note: /dnd and /dm:dnd will both exist until you remove it.")
    else:
        rc = _retire_standalone(args.yes, args.dry_run)

    # 4) Next steps.
    _say()
    _say("Done." if rc == 0 else "Finished with steps skipped.")
    _say("Going forward, invoke the DM with  /dm:dnd  (the old /dnd is retired).")
    _say("If you haven't installed the plugin yet:")
    _say("    /plugin marketplace add neuralinitiative/claude-dnd-skill")
    _say("    /plugin install dm@neural-initiative")
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(1)
