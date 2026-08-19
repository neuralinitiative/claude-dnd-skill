#!/usr/bin/env python3
from __future__ import annotations
"""
build_supplemental.py — fetch and cache non-SRD spell/feature descriptions

Scans character files (or takes explicit names) for spells/features missing from
the SRD, fetches their descriptions from dnd5e.wikidot.com, and writes them to
dnd5e_supplemental.json so the display companion can resolve them locally.

Usage:
    # Scan a campaign's characters and fetch anything missing
    python3 build_supplemental.py --campaign my-campaign

    # Scan a specific character file
    python3 build_supplemental.py --character ~/.claude/dnd/campaigns/my-campaign/characters/aldric.md

    # Add specific entries by name + category
    python3 build_supplemental.py --add "Toll the Dead" spell
    python3 build_supplemental.py --add "Halo of Spores" feature

    # List what's currently in the supplemental file
    python3 build_supplemental.py --list

    # Dry run — show what would be fetched without writing
    python3 build_supplemental.py --campaign X --dry-run
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
from html.parser import HTMLParser

from paths import campaigns_dir as _campaigns_dir, find_campaign as _find_campaign, skill_root as _skill_root
SKILLS_DIR       = str(_skill_root())
CAMPAIGNS_DIR    = str(_campaigns_dir())

# Defaults — overridden per-call once --ruleset is parsed.
DATA_FILE        = os.path.join(SKILLS_DIR, "data", "dnd5e_srd.json")
SUPPLEMENTAL_FILE = os.path.join(SKILLS_DIR, "data", "dnd5e_supplemental.json")


def _set_ruleset_paths(ruleset: str) -> None:
    """Repoint DATA_FILE / SUPPLEMENTAL_FILE module globals based on ruleset."""
    global DATA_FILE, SUPPLEMENTAL_FILE
    if ruleset == "2024":
        DATA_FILE = os.path.join(SKILLS_DIR, "data", "dnd5e_srd_2024.json")
        SUPPLEMENTAL_FILE = os.path.join(SKILLS_DIR, "data", "dnd5e_supplemental_2024.json")
    else:
        DATA_FILE = os.path.join(SKILLS_DIR, "data", "dnd5e_srd.json")
        SUPPLEMENTAL_FILE = os.path.join(SKILLS_DIR, "data", "dnd5e_supplemental.json")

WIKIDOT_BASE = "https://dnd5e.wikidot.com"
FETCH_DELAY  = 0.8   # polite delay between requests


# ─── HTML parser — extract page-content div text ─────────────────────────────

class _WikidotParser(HTMLParser):
    """Extract readable text from dnd5e.wikidot.com page-content div."""

    def __init__(self):
        super().__init__()
        self._depth   = 0
        self._active  = False
        self._parts: list[str] = []
        self._skip_tags = {"script", "style", "nav", "footer", "header"}
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == "div" and attr_dict.get("id") == "page-content":
            self._active = True
            self._depth  = 1
            return
        if self._active:
            if tag == "div":
                self._depth += 1
            if tag in self._skip_tags:
                self._skip_depth += 1
            # Block elements → newline separator
            if tag in ("p", "li", "tr", "h1", "h2", "h3", "h4", "br"):
                self._parts.append("\n")

    def handle_endtag(self, tag):
        if not self._active:
            return
        if tag in self._skip_tags:
            self._skip_depth = max(0, self._skip_depth - 1)
        if tag == "div":
            self._depth -= 1
            if self._depth <= 0:
                self._active = False

    def handle_data(self, data):
        if self._active and self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        # Collapse runs of whitespace/newlines
        lines = [l.strip() for l in raw.splitlines()]
        # Remove empty lines and wikidot boilerplate at the top
        cleaned: list[str] = []
        for line in lines:
            if not line:
                if cleaned and cleaned[-1] != "":
                    cleaned.append("")
            else:
                cleaned.append(line)
        return "\n".join(cleaned).strip()


def _fetch_wikidot(path: str) -> str | None:
    """Fetch a wikidot page and return the cleaned page-content text, or None."""
    url = f"{WIKIDOT_BASE}/{path.lstrip('/')}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dnd5e-supplemental-builder/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        parser = _WikidotParser()
        parser.feed(html)
        text = parser.text()
        return text if len(text) > 50 else None
    except Exception as e:
        print(f"  [fetch error] {url}: {e}", file=sys.stderr)
        return None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ─── Supplemental file I/O ────────────────────────────────────────────────────

def _load_supplemental(ruleset: str = "2014") -> dict:
    if os.path.exists(SUPPLEMENTAL_FILE):
        with open(SUPPLEMENTAL_FILE, encoding="utf-8") as f:
            return json.load(f)
    meta = {
        "description": "Supplemental entries for non-SRD content",
        "ruleset": ruleset,
        "sources": [],
    }
    if ruleset == "2024":
        meta["note"] = (
            "supplemental sources currently 2014-only — wikidot.com has no 2024 "
            "equivalent. Mechanics that match 2014 (e.g. monster manual content) "
            "should be looked up via the 2014 supplemental file."
        )
    return {"_meta": meta, "spells": [], "features": []}


def _save_supplemental(data: dict) -> None:
    with open(SUPPLEMENTAL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Saved → {SUPPLEMENTAL_FILE}")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _in_supplemental(supp: dict, name: str) -> bool:
    slug = _norm(name)
    for cat in ("spells", "features", "equipment", "magic_items", "conditions", "monsters"):
        for r in supp.get(cat, []):
            if _norm(r.get("name", "")) == slug or r.get("index", "") == slug:
                return True
    return False


# ─── SRD index ────────────────────────────────────────────────────────────────

def _load_srd_names() -> set[str]:
    if not os.path.exists(DATA_FILE):
        return set()
    with open(DATA_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    names: set[str] = set()
    for cat, records in raw.items():
        if cat == "_meta" or not isinstance(records, list):
            continue
        for r in records:
            if r.get("name"):
                names.add(_norm(r["name"]))
    return names


# ─── Character file parsing ───────────────────────────────────────────────────

def _extract_names_from_character(path: str) -> list[tuple[str, str]]:
    """Parse a character .md file and return (name, category) pairs to check."""
    entries: list[tuple[str, str]] = []
    if not os.path.exists(path):
        print(f"  [skip] not found: {path}", file=sys.stderr)
        return entries

    with open(path, encoding="utf-8") as f:
        content = f.read()

    # Spells section — lines like "- **Spell Name**" or "- Spell Name"
    spell_section = re.search(r"## (?:Known Cantrips|Prepared Spells|Spells?).*?\n(.*?)(?=\n##|\Z)", content, re.S | re.I)
    if spell_section:
        for m in re.finditer(r"\*\*([^*]+)\*\*|^[-•]\s+([A-Z][^\n(]+?)(?:\s*—|\s*\(|$)", spell_section.group(1), re.M):
            name = (m.group(1) or m.group(2) or "").strip().rstrip("*").strip()
            if name and len(name) > 2:
                entries.append((name, "spell"))

    # Also grab spell names from spellcasting JSON-like sections
    for m in re.finditer(r'"(?:cantrips|prepared)"\s*:\s*\[([^\]]+)\]', content):
        for nm in re.findall(r'"([^"]+)"', m.group(1)):
            entries.append((nm, "spell"))

    # Features section
    feat_section = re.search(r"## Features.*?\n(.*?)(?=\n## |\Z)", content, re.S | re.I)
    if feat_section:
        for m in re.finditer(r"###\s+(.+)|^\*\*([^*]+)\*\*", feat_section.group(1), re.M):
            name = (m.group(1) or m.group(2) or "").strip()
            if name and len(name) > 2 and not name.startswith("*"):
                entries.append((name, "feature"))

    # Attacks — check attack names for lookups
    attack_section = re.search(r"## Attacks.*?\n(.*?)(?=\n## |\Z)", content, re.S | re.I)
    if attack_section:
        for m in re.finditer(r"\|\s*([A-Z][A-Za-z '\-/]+)\s*\|", attack_section.group(1)):
            name = m.group(1).strip()
            if name not in ("Name", "Attack") and len(name) > 2:
                entries.append((name, "spell"))   # may be a spell/cantrip attack

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for name, cat in entries:
        key = (_norm(name), cat)
        if key not in seen:
            seen.add(key)
            unique.append((name, cat))
    return unique


# ─── Fetch and build entry ────────────────────────────────────────────────────

def _wikidot_path(name: str, category: str) -> str:
    slug = _slug(name)
    if category == "spell":
        return f"spell:{slug}"
    if category == "condition":
        return f"condition:{slug}"
    if category == "monster":
        return f"monster:{slug}"
    # For features, try common class paths; fall back to bare slug
    return slug


def _build_entry(name: str, category: str) -> dict | None:
    """Fetch from wikidot and build a supplemental record."""
    path = _wikidot_path(name, category)
    print(f"  Fetching: {WIKIDOT_BASE}/{path}")
    text = _fetch_wikidot(path)

    # For features, wikidot may use class-prefixed paths; try a few
    if not text and category == "feature":
        slug = _slug(name)
        for prefix in ("druid", "rogue", "fighter", "wizard", "cleric", "warlock", "paladin", "ranger", "barbarian", "monk", "bard", "sorcerer"):
            alt_path = f"{prefix}:{slug}"
            print(f"  Retrying:  {WIKIDOT_BASE}/{alt_path}")
            text = _fetch_wikidot(alt_path)
            if text:
                path = alt_path
                break
            time.sleep(FETCH_DELAY)

    if not text:
        print(f"  [miss] No content found for '{name}' — adding stub with wikidot link only")
        text = f"See full description at {WIKIDOT_BASE}/{path}"

    cat_key = "spells" if category == "spell" else "features"
    entry: dict = {
        "name": name,
        "index": _slug(name),
        "description": text,
        "source": "dnd5e.wikidot.com (auto-fetched)",
        "wikidot_url": f"{WIKIDOT_BASE}/{path}",
    }
    if category == "spell":
        entry["level"] = 0      # unknown; display won't show blank level
        entry["school"] = ""
    else:
        entry["class"] = ""

    return entry


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Build/update dnd5e_supplemental.json")
    parser.add_argument("--campaign", metavar="NAME",
        help="Scan all characters in a campaign directory")
    parser.add_argument("--character", metavar="PATH",
        help="Scan a specific character .md file")
    parser.add_argument("--add", nargs=2, metavar=("NAME", "CATEGORY"),
        action="append", default=[],
        help="Add a specific entry: --add 'Toll the Dead' spell")
    parser.add_argument("--list", action="store_true",
        help="List all entries currently in supplemental file")
    parser.add_argument("--dry-run", action="store_true",
        help="Show what would be fetched without writing")
    parser.add_argument("--ruleset", choices=("2014", "2024"), default="2014",
        help="Which ruleset's supplemental file to read/write (default 2014). "
             "2024 has no upstream wikidot equivalent — passing --ruleset 2024 "
             "with no entries to add will write a documented stub.")
    args = parser.parse_args()

    _set_ruleset_paths(args.ruleset)

    if args.ruleset == "2024":
        print(
            "[warn] --ruleset 2024: dnd5e.wikidot.com has no 2024 SRD content yet. "
            "If no entries are queued, a stub file is written for forward-compat.",
            file=sys.stderr,
        )

    supp = _load_supplemental(args.ruleset)

    if args.list:
        print(f"Supplemental entries in {SUPPLEMENTAL_FILE}:")
        for cat in ("spells", "features", "equipment", "magic_items", "conditions", "monsters"):
            records = supp.get(cat, [])
            if records:
                print(f"\n  {cat} ({len(records)}):")
                for r in records:
                    src = r.get("source", "")
                    print(f"    - {r['name']}  [{src}]")
        return

    srd_names = _load_srd_names()
    to_fetch: list[tuple[str, str]] = []   # (name, category)

    # Collect from --add flags
    for name, cat in args.add:
        if not _in_supplemental(supp, name) and _norm(name) not in srd_names:
            to_fetch.append((name, cat))
        elif _in_supplemental(supp, name):
            print(f"  [skip] '{name}' already in supplemental")
        else:
            print(f"  [skip] '{name}' found in SRD")

    # Collect from --character
    if args.character:
        for name, cat in _extract_names_from_character(os.path.expanduser(args.character)):
            if _norm(name) not in srd_names and not _in_supplemental(supp, name):
                to_fetch.append((name, cat))

    # Collect from --campaign
    if args.campaign:
        camp_dir = os.path.join(str(_find_campaign(args.campaign)), "characters")
        if not os.path.isdir(camp_dir):
            print(f"Campaign characters directory not found: {camp_dir}", file=sys.stderr)
            sys.exit(1)
        for fname in os.listdir(camp_dir):
            if fname.endswith(".md"):
                char_path = os.path.join(camp_dir, fname)
                print(f"Scanning {fname}...")
                for name, cat in _extract_names_from_character(char_path):
                    if _norm(name) not in srd_names and not _in_supplemental(supp, name):
                        to_fetch.append((name, cat))

    # Deduplicate
    seen: set[str] = set()
    unique_fetch: list[tuple[str, str]] = []
    for name, cat in to_fetch:
        key = _norm(name)
        if key not in seen:
            seen.add(key)
            unique_fetch.append((name, cat))

    if not unique_fetch:
        # 2024 stub: if the supplemental file doesn't exist yet, write a
        # documented empty stub so downstream loaders don't trip on a missing
        # path and the DM can see "yes, this was intentionally produced".
        if args.ruleset == "2024" and not os.path.exists(SUPPLEMENTAL_FILE):
            _save_supplemental(supp)
            print("Wrote 2024 stub — no upstream fetches available.")
            return
        print("Nothing to fetch — supplemental is up to date.")
        return

    print(f"\n{len(unique_fetch)} entries to fetch:")
    for name, cat in unique_fetch:
        print(f"  [{cat}] {name}")

    if args.dry_run:
        print("\nDry run — nothing written.")
        return

    print()
    added = 0
    for name, cat in unique_fetch:
        entry = _build_entry(name, cat)
        if entry:
            cat_key = "spells" if cat == "spell" else "features"
            supp.setdefault(cat_key, []).append(entry)
            added += 1
        time.sleep(FETCH_DELAY)

    if added:
        _save_supplemental(supp)
        print(f"\nAdded {added} entries to supplemental.")
    else:
        print("\nNo new entries added.")


if __name__ == "__main__":
    main()
