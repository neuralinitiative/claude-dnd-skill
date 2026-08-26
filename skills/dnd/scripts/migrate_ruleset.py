#!/usr/bin/env python3
"""
migrate_ruleset.py — backwards-compat migrator for the 2024 ruleset rollout.

Legacy campaigns (created before the ruleset field existed) have a state.md
header like:

    **Created:** 2026-04-10  **Last session:** 2026-05-05  **Session count:** 26

This script detects that form and offers to inject a `**Ruleset:**` field. It
backs up state.md to state.md.backup-pre-ruleset-YYYYMMDD-HHMMSS before any
write. Idempotent: running on an already-migrated campaign exits cleanly with
"already migrated".

Reads `paths.campaign_ruleset()` for default semantics (2014 if unset). Use
this as a one-shot at /dnd load when a legacy campaign is detected.

Usage:
    # Interactive — prompts the DM
    python3 migrate_ruleset.py <campaign-name>

    # Non-interactive — used by /dnd load and CI
    python3 migrate_ruleset.py <campaign-name> --ruleset 2014 --yes
    python3 migrate_ruleset.py <campaign-name> --check     # exit 0=migrated, 1=needs migration, 2=missing campaign

The `--check` mode is what the procedural /dnd load step calls first to decide
whether to surface the migration prompt to the DM.
"""

from __future__ import annotations

import argparse
import datetime
import shutil
import sys
from pathlib import Path

# Allow running directly from the scripts dir
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import (  # noqa: E402
    DEFAULT_RULESET,
    VALID_RULESETS,
    campaign_ruleset,
    find_campaign,
)
from utf8io import read_text, TextDecodeError  # noqa: E402

# Windows UTF-8 fix: default piped stdout is cp936/GBK, which mangles Chinese
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


HEADER_LINE_PREFIX = "**Created:**"
RULESET_TOKEN = "**Ruleset:**"


def _state_path(campaign: str) -> Path:
    return find_campaign(campaign) / "state.md"


def _has_ruleset_field(text: str) -> bool:
    # Search only the header (first non-empty line that contains **Created:**)
    for line in text.splitlines():
        if HEADER_LINE_PREFIX in line:
            return RULESET_TOKEN in line
    return False


def _inject_ruleset(text: str, ruleset: str) -> str:
    out = []
    injected = False
    for line in text.splitlines():
        if not injected and HEADER_LINE_PREFIX in line and RULESET_TOKEN not in line:
            # Append two-space-separated field at end of header line.
            stripped = line.rstrip()
            line = f"{stripped}  {RULESET_TOKEN} {ruleset}"
            injected = True
        out.append(line)
    if not injected:
        raise RuntimeError(
            "Could not find header line (no '**Created:**' marker). Is this a valid state.md?"
        )
    # Preserve trailing newline if original had one
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _backup(state: Path) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = state.with_name(f"state.md.backup-pre-ruleset-{stamp}")
    shutil.copy2(state, bak)
    return bak


def cmd_check(campaign: str) -> int:
    state = _state_path(campaign)
    if not state.exists():
        print(f"[migrate_ruleset] No state.md at {state}", file=sys.stderr)
        return 2
    text = state.read_text(encoding="utf-8", errors="replace")
    if _has_ruleset_field(text):
        print("migrated")
        return 0
    print("needs-migration")
    return 1


def cmd_migrate(campaign: str, ruleset: str, assume_yes: bool) -> int:
    if ruleset not in VALID_RULESETS:
        print(
            f"[migrate_ruleset] Invalid ruleset '{ruleset}'. Choose from {VALID_RULESETS}.",
            file=sys.stderr,
        )
        return 2

    state = _state_path(campaign)
    if not state.exists():
        print(f"[migrate_ruleset] No state.md at {state}", file=sys.stderr)
        return 2

    try:
        # Lossless read: a legacy-GBK state.md transcodes once to UTF-8, which
        # is exactly what this migration tool is for. Anything undecodable is
        # refused loudly instead of being written back as U+FFFD.
        text = read_text(state)
    except (OSError, TextDecodeError) as e:
        print(f"[migrate_ruleset] {e} — refusing to touch {state}", file=sys.stderr)
        return 2
    if _has_ruleset_field(text):
        # Idempotent path — also surface the actual declared ruleset
        declared = campaign_ruleset(campaign)
        print(f"[migrate_ruleset] Already migrated. Declared ruleset: {declared}")
        return 0

    if not assume_yes:
        print(
            f"\nCampaign '{campaign}' predates the ruleset field.\n"
            f"  Path:   {state}\n"
            f"  Action: stamp '**Ruleset:** {ruleset}' on the header line.\n"
            f"  Backup: state.md.backup-pre-ruleset-<timestamp>\n"
        )
        try:
            ans = input(f"Proceed? [Y/n] ").strip().lower()
        except EOFError:
            ans = ""
        if ans and ans not in ("y", "yes"):
            print("[migrate_ruleset] Cancelled.")
            return 3

    bak = _backup(state)
    new_text = _inject_ruleset(text, ruleset)
    state.write_text(new_text, encoding="utf-8")
    print(
        f"[migrate_ruleset] OK — '{campaign}' stamped as ruleset {ruleset}.\n"
        f"  Backup: {bak}\n"
        f"  Revert: cp '{bak}' '{state}'"
    )
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("campaign", help="Campaign name (directory under campaigns root)")
    p.add_argument(
        "--ruleset",
        default=DEFAULT_RULESET,
        help=f"Ruleset to stamp when migrating (default: {DEFAULT_RULESET}). "
             f"Valid: {'/'.join(VALID_RULESETS)}.",
    )
    p.add_argument(
        "--yes", action="store_true",
        help="Skip the confirmation prompt (used by /dnd load when DM has answered).",
    )
    p.add_argument(
        "--check", action="store_true",
        help="Exit 0=already migrated, 1=needs migration, 2=missing. No write.",
    )
    args = p.parse_args(argv)

    if args.check:
        return cmd_check(args.campaign)
    return cmd_migrate(args.campaign, args.ruleset, args.yes)


if __name__ == "__main__":
    sys.exit(main())
