#!/usr/bin/env python3
"""
sync_srd.py — check upstream sources for updates and rebuild dnd5e_srd.json if stale

Compares the latest commit SHA on both upstream repos against the SHA recorded
in dnd5e_srd.json._meta. Rebuilds only if either source has new commits.

Usage:
    python3 sync_srd.py            # check and rebuild if stale
    python3 sync_srd.py --check    # check only, don't rebuild
    python3 sync_srd.py --force    # always rebuild regardless of SHAs
"""

import json
import os
import subprocess
import sys
import urllib.request

from paths import data_dir as _data_dir
DATA_DIR = str(_data_dir())
OUT_FILE = os.path.join(DATA_DIR, "dnd5e_srd.json")
BUILD_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_srd.py")

APIS = {
    "5e-bits":    "https://api.github.com/repos/5e-bits/5e-database/commits/main?per_page=1",
    "foundryvtt": "https://api.github.com/repos/foundryvtt/dnd5e/commits/master?per_page=1",
}


def _latest_sha(url: str) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dnd-skill-sync/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        if isinstance(data, list) and data:
            return data[0].get("sha", "")
    except Exception as e:
        print(f"  warn: could not reach {url}: {e}")
    return ""


def _stored_meta() -> dict:
    if not os.path.exists(OUT_FILE):
        return {}
    try:
        with open(OUT_FILE, encoding="utf-8") as f:
            return json.load(f).get("_meta", {})
    except Exception:
        return {}


def main() -> None:
    args  = sys.argv[1:]
    check_only = "--check" in args
    force      = "--force" in args

    meta    = _stored_meta()
    sources = meta.get("sources", {})

    if not meta:
        print("Dataset not found — will build from scratch.")
        stale = True
    elif force:
        print("--force: rebuilding regardless of upstream state.")
        stale = True
    else:
        print(f"Checking upstream sources…")
        stale = False
        for src, api_url in APIS.items():
            stored_sha  = sources.get(src, {}).get("sha", "")
            current_sha = _latest_sha(api_url)
            if not current_sha:
                print(f"  {src:<12}  could not fetch — skipping")
                continue
            if stored_sha and current_sha.startswith(stored_sha[:12]) or stored_sha == current_sha:
                print(f"  {src:<12}  up to date  (sha {current_sha[:12]}…)")
            else:
                print(f"  {src:<12}  NEW COMMITS  stored={stored_sha[:12]}…  upstream={current_sha[:12]}…")
                stale = True

    if check_only:
        print()
        print("Stale." if stale else "Up to date. No rebuild needed.")
        return

    if not stale:
        built_at = meta.get("built_at", "?")
        print(f"\nDataset is current (built {built_at}). Nothing to do.")
        print("Use --force to rebuild anyway.")
        return

    print("\nRebuilding dataset…")
    result = subprocess.run(
        [sys.executable, BUILD_PY],
        check=False,
    )
    if result.returncode == 0:
        print("\nSync complete.")
    else:
        print(f"\nBuild failed (exit {result.returncode}). Check output above.")
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
