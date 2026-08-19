#!/usr/bin/env python3
"""
lookup.py — query the bundled dnd5e_srd.json dataset during play

Usage (CLI):
    python3 lookup.py spell "healing word"
    python3 lookup.py item "rapier"
    python3 lookup.py feature "cunning action"
    python3 lookup.py condition "poisoned"
    python3 lookup.py monster "goblin"
    python3 lookup.py <any> "name"       # search across all categories

Flags:
    --all                   show all fuzzy matches, not just the best
    --json                  dump full raw record as JSON
    --campaign <name>       resolve ruleset from the campaign's state.md
    --ruleset 2014|2024     direct ruleset override

Programmatic import (used by app.py):
    from lookup import lookup, lookup_record
    text = lookup("healing word", category="spell")   # → formatted string | None
    rec  = lookup_record("rapier", category="item")   # → dict | None
"""

import difflib
import json
import os
import re
import sys

# paths.py lives alongside this script — import for ruleset resolution
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
try:
    import paths as _paths  # campaign_ruleset, srd_path, DEFAULT_RULESET
except Exception:
    _paths = None

# Resolve the bundled-data dir via paths.py (honors CLAUDE_SKILL_DIR / __file__).
# Fall back to a __file__-relative path if the import somehow failed (this file
# is <skill>/scripts/lookup.py, so data is one level up).
if _paths is not None:
    _DATA_DIR = str(_paths.data_dir())
else:
    _DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data")
DATA_FILE_2014    = os.path.join(_DATA_DIR, "dnd5e_srd.json")
DATA_FILE_2024    = os.path.join(_DATA_DIR, "dnd5e_srd_2024.json")
SUPPLEMENTAL_FILE_2014 = os.path.join(_DATA_DIR, "dnd5e_supplemental.json")
SUPPLEMENTAL_FILE_2024 = os.path.join(_DATA_DIR, "dnd5e_supplemental_2024.json")

# Backwards-compat alias used by older callers (e.g. app.py)
DATA_FILE         = DATA_FILE_2014
SUPPLEMENTAL_FILE = SUPPLEMENTAL_FILE_2014

# Category aliases → canonical dataset key
CATEGORY_MAP = {
    "spell":       "spells",
    "spells":      "spells",
    "equipment":   "equipment",
    "gear":        "equipment",
    "magic_item":  "magic_items",
    "magic":       "magic_items",
    "magic_items": "magic_items",
    "item":        None,   # searches equipment + magic_items
    "items":       None,
    "condition":   "conditions",
    "conditions":  "conditions",
    "monster":     "monsters",
    "monsters":    "monsters",
    "feature":     "features",
    "features":    "features",
    "feat":        "features",
}

ALL_CATEGORIES = ["spells", "equipment", "magic_items", "conditions", "monsters", "features"]

# ─── Data loading / index ─────────────────────────────────────────────────────

# Per-ruleset caches keyed by '2014' / '2024'
_data_by_rs: dict = {}            # {ruleset: {category: [records]}}
_index_by_rs: dict = {}           # {ruleset: {category: {norm_name: record}}}
_meta_by_rs: dict = {}            # {ruleset: {_meta dict}}
_active_ruleset: str = "2014"     # which dataset _data/_index point at


def _srd_path_for(ruleset: str) -> str:
    if ruleset == "2024":
        return DATA_FILE_2024
    return DATA_FILE_2014


def _supp_path_for(ruleset: str) -> str:
    if ruleset == "2024":
        return SUPPLEMENTAL_FILE_2024
    return SUPPLEMENTAL_FILE_2014


def _load_ruleset(ruleset: str) -> None:
    """Load (and cache) the dataset for the given ruleset."""
    if ruleset in _data_by_rs:
        return

    data: dict = {}
    meta: dict = {}
    srd_file = _srd_path_for(ruleset)

    if os.path.exists(srd_file):
        with open(srd_file, encoding="utf-8") as f:
            raw = json.load(f)
        for k, v in raw.items():
            if k == "_meta":
                meta = v if isinstance(v, dict) else {}
            else:
                data[k] = list(v)  # copy so we can safely extend

    # Merge supplemental (non-SRD content) — adds without overwriting SRD entries
    supp_file = _supp_path_for(ruleset)
    if os.path.exists(supp_file):
        with open(supp_file, encoding="utf-8") as f:
            supp = json.load(f)
        for k, v in supp.items():
            if k == "_meta" or not isinstance(v, list):
                continue
            existing_names = {_norm(r.get("name", "")) for r in data.get(k, [])}
            for r in v:
                if _norm(r.get("name", "")) not in existing_names:
                    data.setdefault(k, []).append(r)

    index: dict = {}
    for cat, records in data.items():
        idx = {}
        for r in records:
            name = r.get("name", "")
            key = _norm(name)
            idx[key] = r
            if r.get("index") and r["index"] != key:
                idx[r["index"]] = r
        index[cat] = idx

    _data_by_rs[ruleset] = data
    _index_by_rs[ruleset] = index
    _meta_by_rs[ruleset] = meta


def _set_active(ruleset: str) -> None:
    """Set the active ruleset for module-level lookup() / lookup_record() calls."""
    global _active_ruleset
    if ruleset not in ("2014", "2024"):
        ruleset = "2014"
    _load_ruleset(ruleset)
    _active_ruleset = ruleset


# ── Backwards-compat shim — older callers expect _load() and module globals ──
_data: dict = {}
_index: dict = {}
_loaded = False


def _load() -> None:
    """Load the active ruleset (default 2014) and refresh module-level views."""
    global _data, _index, _loaded
    _load_ruleset(_active_ruleset)
    _data = _data_by_rs.get(_active_ruleset, {})
    _index = _index_by_rs.get(_active_ruleset, {})
    _loaded = True


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# ─── Matching ─────────────────────────────────────────────────────────────────

def _score(query: str, record: dict) -> int:
    """3=exact, 2=starts-with, 1=contains, 0=no match."""
    q    = _norm(query)
    name = _norm(record.get("name", ""))
    idx  = record.get("index", "")
    if name == q or idx == q:
        return 3
    if name.startswith(q) or idx.startswith(q):
        return 2
    if q in name or q in idx:
        return 1
    return 0


def _find(query: str, records: list, top_n: int = 1):
    scored = [(r, _score(query, r)) for r in records]
    scored = [(r, s) for r, s in scored if s > 0]
    scored.sort(key=lambda x: (-x[1], x[0].get("name", "")))
    return [r for r, _ in scored[:top_n]]


def _get_records(cat_key, ruleset: str = None):
    """Return all records for a category key. None → equipment + magic_items."""
    rs = ruleset or _active_ruleset
    _load_ruleset(rs)
    data = _data_by_rs.get(rs, {})
    if cat_key is None:
        return data.get("equipment", []) + data.get("magic_items", [])
    return data.get(cat_key, [])


# ─── Formatters ───────────────────────────────────────────────────────────────

def _fmt_spell(r: dict) -> str:
    lvl    = r.get("level", 0)
    school = r.get("school", "")
    lvl_s  = "Cantrip" if lvl == 0 else f"Level {lvl}"
    lines  = [f"## {r.get('name','?')}  [{lvl_s} {school}]", ""]
    comp   = ", ".join(r.get("components", []))
    if "M" in r.get("components", []) and r.get("material"):
        comp += f" ({r['material']})"
    lines += [
        f"Casting time : {r.get('casting_time','')}",
        f"Range        : {r.get('range','')}",
        f"Components   : {comp}",
        f"Duration     : {r.get('duration','')}"
        + ("  *(concentration)*" if r.get("concentration") else ""),
        f"Ritual       : {'Yes' if r.get('ritual') else 'No'}",
    ]
    classes = r.get("classes", [])
    if classes:
        lines += ["", f"Classes: {', '.join(classes)}"]
    desc = r.get("description", "")
    if desc:
        lines += ["", desc]
    hl = r.get("higher_level", "")
    if hl:
        lines += ["", "**At Higher Levels:**", hl]
    return "\n".join(lines)


def _fmt_equipment(r: dict) -> str:
    lines = [f"## {r.get('name','?')}  [{r.get('category','')}]", ""]
    if r.get("cost"):
        lines.append(f"Cost       : {r['cost']}")
    if r.get("weight") is not None:
        lines.append(f"Weight     : {r['weight']} lb")
    if r.get("damage"):
        lines.append(f"Damage     : {r['damage']}")
    if r.get("damage_2h"):
        lines.append(f"2H Damage  : {r['damage_2h']}")
    if r.get("ac"):
        lines.append(f"Armour     : {r['ac']}")
    if r.get("properties"):
        lines.append(f"Properties : {', '.join(r['properties'])}")
    if r.get("range"):
        lines.append(f"Range      : {r['range']}")
    if r.get("throw_range"):
        lines.append(f"Throw      : {r['throw_range']}")
    if r.get("stealth_disadv"):
        lines.append("Stealth    : disadvantage")
    if r.get("str_minimum"):
        lines.append(f"Str min    : {r['str_minimum']}")
    desc = r.get("description", "")
    if desc:
        lines += ["", desc]
    return "\n".join(lines)


def _fmt_magic_item(r: dict) -> str:
    lines = [f"## {r.get('name','?')}  [{r.get('rarity','')} {r.get('category','')}]", ""]
    if r.get("attunement"):
        lines.append("Requires attunement.")
        lines.append("")
    lines.append(r.get("description", ""))
    return "\n".join(lines)


def _fmt_condition(r: dict) -> str:
    lines = [f"## {r.get('name','?')}", ""]
    for bullet in r.get("description", "").splitlines():
        lines.append(f"  • {bullet}" if bullet.strip() and not bullet.startswith("•") else bullet)
    return "\n".join(lines)


def _fmt_monster(r: dict) -> str:
    lines = [f"## {r.get('name','?')}  [CR {r.get('cr','?')} | {r.get('xp','?')} XP]",
             f"{r.get('size','')} {r.get('type','')}  ·  {r.get('alignment','')}",
             "", f"AC {r.get('ac','?')}  ·  HP {r.get('hp','?')} ({r.get('hp_dice','')})",
             f"Speed: {r.get('speed','')}", ""]
    abbr = ["STR","DEX","CON","INT","WIS","CHA"]
    keys = ["str","dex","con","int","wis","cha"]
    def _mod(v): return (v - 10) // 2
    row1 = " | ".join(f"{a:3}" for a in abbr)
    row2 = " | ".join(f"{r.get(k,10):3}({_mod(r.get(k,10)):+d})" for k in keys)
    lines += [row1, row2, ""]
    if r.get("languages"):
        lines.append(f"Languages: {r['languages']}")
    desc = r.get("description", "")
    if desc:
        lines += ["", desc]
    return "\n".join(lines)


def _fmt_feature(r: dict) -> str:
    cls_s   = r.get("class", "")
    lvl_s   = f"  (level {r['level_req']})" if r.get("level_req") else ""
    src_s   = f"{cls_s}{lvl_s}".strip() or r.get("type", "")
    lines   = [f"## {r.get('name','?')}  [{src_s}]", "", r.get("description", "")]
    return "\n".join(lines)


FORMATTERS = {
    "spells":      _fmt_spell,
    "equipment":   _fmt_equipment,
    "magic_items": _fmt_magic_item,
    "conditions":  _fmt_condition,
    "monsters":    _fmt_monster,
    "features":    _fmt_feature,
}


# ─── Wikidot fallback URL ─────────────────────────────────────────────────────

def wikidot_url(name: str, category: str = None, record: dict = None) -> str:
    """Return a wikidot.com URL for a name that wasn't found in the dataset.

    Uses the record's own wikidot_url field if present (supplemental entries),
    otherwise constructs a URL from the category and name slug.
    Falls back to a site search for unknown categories.
    """
    if record and record.get("wikidot_url"):
        return record["wikidot_url"]

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    # Map internal category keys back to wikidot path prefixes
    _PREFIXES = {
        "spells":     "spell",
        "spell":      "spell",
        "conditions": "condition",
        "condition":  "condition",
        "monsters":   "monster",
        "monster":    "monster",
        "equipment":  "equipment",
        "magic_items": "magic-items",
    }
    prefix = _PREFIXES.get(category or "")
    if prefix:
        return f"https://dnd5e.wikidot.com/{prefix}:{slug}"
    # For features and unknowns: direct slug URL (wikidot search is unavailable)
    return f"https://dnd5e.wikidot.com/{slug}"


# ─── Public API ───────────────────────────────────────────────────────────────

def _fallback_categories(ruleset: str) -> set:
    """Return the set of categories that should fall back to 2014 when missing
    in the requested ruleset's dataset (per `_meta.fallback_2014`)."""
    meta = _meta_by_rs.get(ruleset, {}) or {}
    fb = meta.get("fallback_2014") or []
    if isinstance(fb, list):
        return set(fb)
    return set()


def _find_in_ruleset(query: str, cat_key, ruleset: str, top_n: int = 1):
    """Scan the dataset for `ruleset` for matches; if cat_key is given and the
    primary search misses, also scan 2014 when that category is in the
    ruleset's fallback list."""
    records = _get_records(cat_key, ruleset=ruleset)
    results = _find(query, records, top_n=top_n)
    if results:
        return results, ruleset, False

    # Resolve fallback for category-specific lookups
    if cat_key is not None and ruleset == "2024" and cat_key in _fallback_categories("2024"):
        fb_records = _get_records(cat_key, ruleset="2014")
        fb_results = _find(query, fb_records, top_n=top_n)
        if fb_results:
            return fb_results, "2014", True

    return [], ruleset, False


def lookup_record(query: str, category=None, ruleset=None):
    """Return the best-matching record dict, or None.

    `ruleset` overrides the module-level active ruleset if supplied.
    The returned record is annotated with `_cat`, `_ruleset`, and `_fallback`.
    """
    rs = ruleset or _active_ruleset
    _load_ruleset(rs)
    if not _data_by_rs.get(rs):
        return None
    cat_key = CATEGORY_MAP.get((category or "").lower()) if category else None
    results, hit_rs, fb = _find_in_ruleset(query, cat_key, rs, top_n=1)

    resolved_cat = cat_key
    if not results and not category:
        # Search every category in the active ruleset
        for ck in ALL_CATEGORIES:
            results = _find(query, _data_by_rs.get(rs, {}).get(ck, []), top_n=1)
            if results:
                resolved_cat = ck
                hit_rs = rs
                break
        # Fallback for 2024 cross-category — scan fallback categories in 2014
        if not results and rs == "2024":
            for ck in ALL_CATEGORIES:
                if ck not in _fallback_categories("2024"):
                    continue
                results = _find(query, _data_by_rs.get("2014", {}).get(ck, []), top_n=1)
                if results:
                    resolved_cat = ck
                    hit_rs = "2014"
                    fb = True
                    break

    # item search — resolve sub-category and tag the record
    if results and cat_key is None and resolved_cat is None:
        rec = results[0]
        for ck in ["equipment", "magic_items"]:
            if rec in _data_by_rs.get(hit_rs, {}).get(ck, []):
                resolved_cat = ck
                break

    if results and resolved_cat:
        results[0]["_cat"] = resolved_cat
        results[0]["_ruleset"] = hit_rs
        results[0]["_fallback"] = fb
    return results[0] if results else None


def lookup(query: str, category=None, ruleset=None):
    """Return a formatted string description for the best match, or None."""
    rec = lookup_record(query, category=category, ruleset=ruleset)
    if not rec:
        return None
    cat = rec.get("_cat") or "spells"
    fmt = FORMATTERS.get(cat, lambda r: json.dumps(r, indent=2))
    text = fmt(rec)
    if rec.get("_fallback"):
        text += "\n\n_[2014 fallback]_"
    return text


def _apply_level(text: str, level: int) -> str:
    """Collapse any scale progression strings to the value for the given level.

    Matches patterns like:
        1d6 (lvl 1–2), 2d6 (lvl 3–4), ..., 10d6 (lvl 19–20)
        +2 (lvl 1–8), +3 (lvl 9–15), +4 (lvl 16–20)

    Replaces the entire comma-separated run with just the matching value.
    Entries where the end bound is implicit (last entry, no upper bound shown)
    are treated as extending to 20.
    """
    # One entry: "VALUE (lvl START)" or "VALUE (lvl START–END)"
    entry_pat = r'[+\w\d/]+\s+\(lvl\s+\d+(?:[–\-]\d+)?\)'
    # Two or more entries separated by ", "
    scale_pat = entry_pat + r'(?:,\s*' + entry_pat + r')+'

    def _pick(m):
        entries = re.findall(r'([+\w\d/]+)\s+\(lvl\s+(\d+)(?:[–\-](\d+))?\)', m.group(0))
        for i, (val, start_s, end_s) in enumerate(entries):
            start = int(start_s)
            # Last entry: if no explicit end, treat as lvl START–20
            end = int(end_s) if end_s else 20
            if start <= level <= end:
                return val
        return m.group(0)  # no match — leave as-is

    return re.sub(scale_pat, _pick, text)


def lookup_with_level(query: str, category=None, level=None, ruleset=None):
    """lookup() variant that collapses scale progressions to the given character level."""
    text = lookup(query, category=category, ruleset=ruleset)
    if text and level:
        try:
            text = _apply_level(text, int(level))
        except (ValueError, TypeError):
            pass
    return text


# ─── Near-miss suggestions ("did you mean?") ──────────────────────────────────

def _suggest_categories(category) -> list:
    """Resolve a category token to the dataset keys a suggestion pass should
    scan. `None` / unknown → all categories; the `item` pseudo-category →
    equipment + magic_items; a concrete key → just that key."""
    if not category:
        return list(ALL_CATEGORIES)
    cat = category.lower()
    if cat in ("item", "items"):
        return ["equipment", "magic_items"]
    key = CATEGORY_MAP.get(cat)
    if key is None:
        return list(ALL_CATEGORIES)
    return [key]


def suggest(query: str, category=None, ruleset=None, n: int = 3, cutoff: float = 0.6):
    """Return up to `n` near-miss name suggestions for a query that didn't match.

    Powers a "did you mean?" hint when an exact/substring lookup dead-ends on a
    typo ("poisonned" → "poisoned", "fireballl" → "fireball"). Matching is done
    on normalized names (difflib ratio ≥ cutoff), plus a token-containment pass
    that catches a good word buried in a longer query. Returns a list of
    (name, category) tuples, best first, deduped by name.
    """
    rs = ruleset or _active_ruleset
    _load_ruleset(rs)
    data = _data_by_rs.get(rs, {})
    if not data:
        return []

    q = _norm(query)
    if not q:
        return []

    # normalized-name → (original_name, category), first writer wins
    candidates: dict = {}
    for ck in _suggest_categories(category):
        for r in data.get(ck, []):
            nm = r.get("name", "")
            if nm:
                candidates.setdefault(_norm(nm), (nm, ck))

    keys = list(candidates.keys())
    ranked: list = []
    seen: set = set()

    # 1) fuzzy — closest normalized names above the cutoff
    for k in difflib.get_close_matches(q, keys, n=n * 2, cutoff=cutoff):
        nm, ck = candidates[k]
        if nm.lower() not in seen:
            seen.add(nm.lower())
            ranked.append((nm, ck))

    # 2) token-containment — a full query word that is (or starts) a name word,
    #    e.g. "posion status" nothing fuzzy but "poison" tokens overlap. Cheap
    #    backstop that only fires when fuzzy under-delivers.
    if len(ranked) < n:
        q_tokens = set(t for t in q.split("-") if len(t) >= 4)
        for k in keys:
            nm, ck = candidates[k]
            if nm.lower() in seen:
                continue
            name_tokens = set(k.split("-"))
            if q_tokens & name_tokens:
                seen.add(nm.lower())
                ranked.append((nm, ck))
            if len(ranked) >= n:
                break

    return ranked[:n]


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _parse_value_flag(flags_with_args, name):
    """Extract --name VALUE or --name=VALUE from a list of argv tokens.
    Returns (value or None, leftover list with the flag stripped)."""
    out = []
    val = None
    skip = False
    for i, tok in enumerate(flags_with_args):
        if skip:
            skip = False
            continue
        if tok == name:
            if i + 1 < len(flags_with_args):
                val = flags_with_args[i + 1]
                skip = True
        elif tok.startswith(name + "="):
            val = tok.split("=", 1)[1]
        else:
            out.append(tok)
    return val, out


def main() -> None:
    raw = sys.argv[1:]

    # Pull out value-bearing flags first so the positional parser doesn't see them
    campaign_arg, raw = _parse_value_flag(raw, "--campaign")
    ruleset_arg, raw  = _parse_value_flag(raw, "--ruleset")

    # Remaining --bool flags
    flags = [a for a in raw if a.startswith("--")]
    args  = [a for a in raw if not a.startswith("--")]
    dump_json = "--json" in flags
    show_all  = "--all"  in flags
    top_n     = 10 if show_all else 1

    # ── Resolve ruleset ───────────────────────────────────────────────────
    ruleset = None
    if ruleset_arg:
        if ruleset_arg not in ("2014", "2024"):
            print(f"--ruleset must be 2014 or 2024 (got {ruleset_arg!r})", file=sys.stderr)
            sys.exit(2)
        ruleset = ruleset_arg
    elif campaign_arg:
        if _paths is None:
            print("paths.py unavailable — cannot resolve --campaign", file=sys.stderr)
            sys.exit(2)
        ruleset = _paths.campaign_ruleset(campaign_arg)
    else:
        # Default 2014, but emit a hint if 2024 is on disk
        ruleset = "2014"
        if os.path.exists(DATA_FILE_2024):
            print(
                "# lookup.py: defaulting to 2014 ruleset "
                "(use --campaign or --ruleset to switch)",
                file=sys.stderr,
            )

    srd_file = _srd_path_for(ruleset)
    if not os.path.exists(srd_file):
        print(f"Dataset not found: {srd_file}")
        _build = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_srd.py")
        if ruleset == "2024":
            print(f'Run: python3 "{_build}" --ruleset 2024   (or: /dnd data sync --ruleset 2024)')
        else:
            print(f'Run: python3 "{_build}"   (or: /dnd data sync)')
        sys.exit(1)

    _set_active(ruleset)

    if len(args) < 2:
        print(__doc__)
        sys.exit(0)

    category, query = args[0].lower(), " ".join(args[1:])
    cat_key  = CATEGORY_MAP.get(category)
    cat_specified = category in CATEGORY_MAP

    if not cat_specified:
        # Treat as a query across all categories
        query = " ".join(args)
        cat_key = None

    # Search the active ruleset; fall back to 2014 for categories listed
    # in the active ruleset's _meta.fallback_2014 when the category is given.
    fallback_used = False
    if cat_specified:
        results, hit_rs, fb = _find_in_ruleset(query, cat_key, ruleset, top_n=top_n)
        fallback_used = fb
    else:
        records = []
        for ck in ALL_CATEGORIES:
            records.extend(_data_by_rs.get(ruleset, {}).get(ck, []))
        results = _find(query, records, top_n=top_n)
        hit_rs  = ruleset
        # If nothing in 2024, try 2014 across fallback categories
        if not results and ruleset == "2024":
            fb_records = []
            for ck in ALL_CATEGORIES:
                if ck in _fallback_categories("2024"):
                    fb_records.extend(_data_by_rs.get("2014", {}).get(ck, []))
            results = _find(query, fb_records, top_n=top_n)
            if results:
                hit_rs = "2014"
                fallback_used = True

    # For item searches, resolve which sub-category each result came from
    def _resolve_cat(record, rs):
        if cat_key is not None:
            return cat_key
        for ck in ALL_CATEGORIES:
            if record in _data_by_rs.get(rs, {}).get(ck, []):
                return ck
        return "spells"

    if not results:
        print(f"No match for '{query}' in {category}.")
        hints = suggest(query, category=category if cat_specified else None, ruleset=hit_rs)
        if hints:
            pretty = ", ".join(
                f"{nm} ({ck.rstrip('s').replace('_', ' ')})" for nm, ck in hints
            )
            print(f"Did you mean: {pretty}?")
        sys.exit(0)

    for r in results:
        if dump_json:
            out = dict(r)
            out["_ruleset"] = hit_rs
            out["_fallback"] = fallback_used
            print(json.dumps(out, indent=2))
        else:
            rcat = _resolve_cat(r, hit_rs)
            fmt  = FORMATTERS.get(rcat, lambda x: json.dumps(x, indent=2))
            text = fmt(r)
            if fallback_used:
                text += "  [2014 fallback]"
            print(text)
            print()


if __name__ == "__main__":
    main()
