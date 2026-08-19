#!/usr/bin/env python3
"""
data_pull.py — fetch 5e SRD data files from 5e-bits/5e-database

Downloads five targeted JSON datasets from the open 5e-bits/5e-database repo
(MIT + OGL licensed) and saves them to data/.

Usage:
    python3 data_pull.py           # skip files that exist
    python3 data_pull.py --force   # re-download everything
    python3 data_pull.py --status  # show what's installed
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

from paths import data_dir as _data_dir
DATA_DIR = str(_data_dir())
BASE_URL  = "https://raw.githubusercontent.com/5e-bits/5e-database/main/src/2014"
META_FILE = os.path.join(DATA_DIR, "meta.json")

TARGETS = [
    {
        "key":      "monsters",
        "filename": "5e-SRD-Monsters.json",
        "desc":     "Monsters — CR, HP, AC, attacks, abilities",
    },
    {
        "key":      "spells",
        "filename": "5e-SRD-Spells.json",
        "desc":     "Spells — level, school, range, duration, components, description",
    },
    {
        "key":      "magic_items",
        "filename": "5e-SRD-Magic-Items.json",
        "desc":     "Magic items — rarity, attunement, description",
    },
    {
        "key":      "conditions",
        "filename": "5e-SRD-Conditions.json",
        "desc":     "Conditions — poisoned, charmed, frightened, etc.",
    },
    {
        "key":      "equipment",
        "filename": "5e-SRD-Equipment.json",
        "desc":     "Equipment — weapons, armour, adventuring gear, costs",
    },
]


def _load_meta() -> dict:
    try:
        with open(META_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_meta(meta: dict) -> None:
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def _fetch(url: str, dest: str) -> tuple[bool, str]:
    """Download url → dest. Returns (success, message)."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "dnd-skill-data-pull/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        # Validate it's parseable JSON before writing
        json.loads(data)
        with open(dest, "wb") as f:
            f.write(data)
        kb = len(data) // 1024
        return True, f"{kb} KB"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, f"Network error: {e.reason}"
    except json.JSONDecodeError:
        return False, "Invalid JSON in response"
    except Exception as e:
        return False, str(e)


def cmd_status() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    meta = _load_meta()
    print(f"Data directory: {DATA_DIR}\n")
    any_missing = False
    for t in TARGETS:
        path = os.path.join(DATA_DIR, t["filename"])
        if os.path.exists(path):
            size_kb = os.path.getsize(path) // 1024
            pulled = meta.get(t["key"], {}).get("pulled_at", "unknown date")
            print(f"  ✓  {t['key']:15s}  {size_kb:>5} KB   pulled {pulled}")
        else:
            print(f"  ✗  {t['key']:15s}  (not downloaded)")
            any_missing = True
    if any_missing:
        print("\nRun `data_pull.py` to download missing files.")
    else:
        print("\nAll files present. Run `data_pull.py --force` to refresh.")


def cmd_pull(force: bool) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    meta = _load_meta()

    pulled = 0
    skipped = 0
    failed = 0

    for t in TARGETS:
        dest = os.path.join(DATA_DIR, t["filename"])
        url  = f"{BASE_URL}/{t['filename']}"

        if os.path.exists(dest) and not force:
            print(f"  skip  {t['key']} (already exists — use --force to refresh)")
            skipped += 1
            continue

        print(f"  pull  {t['key']} … ", end="", flush=True)
        t0 = time.time()
        ok, msg = _fetch(url, dest)
        elapsed = time.time() - t0

        if ok:
            print(f"done ({msg}, {elapsed:.1f}s)")
            meta[t["key"]] = {
                "filename":  t["filename"],
                "pulled_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "url":       url,
            }
            pulled += 1
        else:
            print(f"FAILED — {msg}")
            failed += 1

    _save_meta(meta)

    print()
    print(f"Done. Pulled: {pulled}  Skipped: {skipped}  Failed: {failed}")
    if failed:
        print("Check network connection and try again. Partial pulls are safe to re-run.")
    else:
        print(f"Data stored in: {DATA_DIR}")
        print("Use `lookup.py <category> <name>` to query during play.")


def main() -> None:
    args = sys.argv[1:]
    if "--status" in args:
        cmd_status()
    else:
        cmd_pull(force="--force" in args)


if __name__ == "__main__":
    main()
