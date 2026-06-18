#!/usr/bin/env python3
"""
install_autosave_hook.py — opt-in installer for the autosave Stop hook.

The autosave checkpoint (`autosave_checkpoint.py`) only runs automatically if a
Claude Code **Stop hook** invokes it. This installer registers that hook in the
user settings file (`~/.claude/settings.json` by default), idempotently.

It is **opt-in and off by default** — the skill never edits your settings
without you running this. The in-model micro-save cadence (see SKILL.md) works
without it; this hook is the deterministic backstop on top.

Usage:
  python3 install_autosave_hook.py            # install (idempotent)
  python3 install_autosave_hook.py --uninstall
  python3 install_autosave_hook.py --status
  python3 install_autosave_hook.py --settings <path>   # target a specific file

Once installed, toggle it per-campaign with `/dm:dnd autosave on|off` — the hook
reads the `autosave` flag from the active campaign's state.md and no-ops when off
or when no D&D campaign is loaded.
"""

import sys
import os
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

HOOK_EVENT = "Stop"


def checkpoint_command() -> str:
    """Absolute command the hook runs. Resolved at install time."""
    script = paths.scripts_dir() / "autosave_checkpoint.py"
    return f'python3 "{script}"'


def default_settings_path() -> str:
    return os.path.expanduser("~/.claude/settings.json")


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError) as e:
        print(f"Error: cannot parse {path}: {e}", file=sys.stderr)
        sys.exit(1)


def _dump(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _our_command_marker() -> str:
    """Substring that identifies our hook regardless of the resolved path."""
    return "autosave_checkpoint.py"


def _stop_groups(settings: dict) -> list:
    return settings.setdefault("hooks", {}).setdefault(HOOK_EVENT, [])


def is_installed(settings: dict) -> bool:
    for group in settings.get("hooks", {}).get(HOOK_EVENT, []):
        for h in group.get("hooks", []):
            if _our_command_marker() in str(h.get("command", "")):
                return True
    return False


def install(path: str) -> None:
    settings = _load(path)
    if is_installed(settings):
        print(f"Already installed in {path} — nothing to do.")
        return
    _stop_groups(settings).append({
        "matcher": "",
        "hooks": [{"type": "command", "command": checkpoint_command()}],
    })
    _dump(path, settings)
    print(f"Installed autosave Stop hook in {path}.")
    print(f"  command: {checkpoint_command()}")
    print("Toggle per-campaign with /dm:dnd autosave on|off.")


def uninstall(path: str) -> None:
    settings = _load(path)
    groups = settings.get("hooks", {}).get(HOOK_EVENT, [])
    changed = False
    new_groups = []
    for group in groups:
        kept = [h for h in group.get("hooks", [])
                if _our_command_marker() not in str(h.get("command", ""))]
        if len(kept) != len(group.get("hooks", [])):
            changed = True
        if kept:
            group["hooks"] = kept
            new_groups.append(group)
        elif not group.get("hooks"):
            changed = True  # drop now-empty group
    if not changed:
        print(f"Not installed in {path} — nothing to remove.")
        return
    settings["hooks"][HOOK_EVENT] = new_groups
    if not settings["hooks"][HOOK_EVENT]:
        del settings["hooks"][HOOK_EVENT]
    if not settings["hooks"]:
        del settings["hooks"]
    _dump(path, settings)
    print(f"Removed autosave Stop hook from {path}.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the autosave Stop hook.")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--settings", default=None, help="Settings file to edit.")
    args = parser.parse_args()

    path = args.settings or default_settings_path()

    if args.status:
        print(f"settings: {path}")
        print(f"installed: {is_installed(_load(path))}")
        print(f"command:   {checkpoint_command()}")
        return 0
    if args.uninstall:
        uninstall(path)
        return 0
    install(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
