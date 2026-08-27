#!/usr/bin/env python3
"""
build_srd.py — build the bundled dnd5e_srd.json from two upstream sources

Sources:
  • 5e-bits/5e-database  (MIT + OGL)    — spells, equipment, magic items, conditions, monsters
  • foundryvtt/dnd5e     (MIT + CC-BY-4.0) — class features, racial traits (2024 SRD)

Output: data/dnd5e_srd.json

Usage:
    python3 build_srd.py             # build/rebuild the dataset
    python3 build_srd.py --status    # show current dataset metadata
    python3 build_srd.py --no-fvtt   # skip FoundryVTT features (faster, spells/items only)
"""

from __future__ import annotations  # PEP 604 annotations on Python 3.9

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

try:
    import yaml
except ImportError:
    print("PyYAML required for FoundryVTT data. Install: pip3 install pyyaml")
    print("Run with --no-fvtt to skip class features and build spells/items only.")
    yaml = None  # type: ignore

from paths import data_dir as _data_dir
DATA_DIR  = str(_data_dir())

# Ruleset configuration. 2014 is the historical default; 2024 is partial
# upstream (spells not yet in 5e-bits 2024 dir as of build date — those
# fall back to the 2014 dataset, recorded in _meta.fallback_2014).
RULESET_2014 = "2014"
RULESET_2024 = "2024"
DEFAULT_RULESET = RULESET_2014

RAW_5EBITS_BASE = "https://raw.githubusercontent.com/5e-bits/5e-database/main/src"
RAW_FVTT     = "https://raw.githubusercontent.com/foundryvtt/dnd5e/master"
FVTT_TREE    = "https://api.github.com/repos/foundryvtt/dnd5e/git/trees/master?recursive=1"
BITS_COMMITS = "https://api.github.com/repos/5e-bits/5e-database/commits/main?per_page=1"
FVTT_COMMITS = "https://api.github.com/repos/foundryvtt/dnd5e/commits/master?per_page=1"

# 2014 file roster — full coverage on upstream
BITS_FILES_2014 = {
    "spells":      "5e-SRD-Spells.json",
    "equipment":   "5e-SRD-Equipment.json",
    "magic_items": "5e-SRD-Magic-Items.json",
    "conditions":  "5e-SRD-Conditions.json",
    "monsters":    "5e-SRD-Monsters.json",
}

# 2024 file roster — partial upstream coverage as of 2026-05.
# Categories NOT present here fall back to 2014 (see _build_5ebits).
BITS_FILES_2024 = {
    "equipment":                  "5e-SRD-Equipment.json",
    "magic_items":                "5e-SRD-Magic-Items.json",
    "conditions":                 "5e-SRD-Conditions.json",
    "monsters":                   "5e-SRD-Monsters.json",
    "weapon_mastery_properties":  "5e-SRD-Weapon-Mastery-Properties.json",
    "species":                    "5e-SRD-Species.json",
    "subspecies":                 "5e-SRD-Subspecies.json",
    "backgrounds":                "5e-SRD-Backgrounds.json",
    "feats":                      "5e-SRD-Feats.json",
}

# 2024 spells are sourced from foundryvtt/dnd5e packs/_source/spells24/
# (CC-BY-4.0 licensed, 352 spells, more complete than 5e-bits 2024 dir
# which is missing spells as of build date).
FVTT_SPELLS_2024_PATH = "packs/_source/spells24"

# 2024 monsters are sourced from foundryvtt/dnd5e packs/_source/actors24/
# (CC-BY-4.0 licensed, ~436 actors). 5e-bits 2024 dir only contains 3
# native 2024 monsters as of build date; foundry's actors24 provides full
# coverage. Only used for ruleset=2024.
FVTT_ACTORS_2024_PATH = "packs/_source/actors24"

# Categories that fall back to 2014 augmentation when building 2024.
# Reasoning per category:
#   monsters — WotC 2024 SRD 5.2 only ships 3 monsters; 2014 SRD has 334.
#              Augment with 2014 so the bestiary is usable. Native 2024
#              records keep their 5e-bits source marker; augmented records
#              carry _augmented_from_2014: true.
# v2 candidate: pull from foundryvtt/dnd5e packs/_source/actors24/ (466
# files) for native 2024 monsters once a normaliser is written.
RULESET_2024_FALLBACK: tuple = ("monsters",)


def _bits_url(ruleset: str) -> str:
    """Return the 5e-bits raw URL for a given ruleset."""
    sub = "2024/en" if ruleset == RULESET_2024 else "2014/en"
    return f"{RAW_5EBITS_BASE}/{sub}"


def _out_file(ruleset: str) -> str:
    """Return output path for the given ruleset's compiled SRD."""
    name = "dnd5e_srd_2024.json" if ruleset == RULESET_2024 else "dnd5e_srd.json"
    return os.path.join(DATA_DIR, name)


# Module-level handles kept for backwards compatibility with existing
# normalisers below. They get re-bound by cmd_build() when --ruleset is set.
RAW_5EBITS = _bits_url(DEFAULT_RULESET)
OUT_FILE   = _out_file(DEFAULT_RULESET)
BITS_FILES = BITS_FILES_2014


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def _fetch(url: str, as_json: bool = False):
    """Fetch URL, return parsed JSON or raw text. Returns None on error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dnd-skill-build/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        return json.loads(data) if as_json else data.decode("utf-8")
    except Exception as e:
        print(f"    ✗ {url}: {e}", file=sys.stderr)
        return None


def _fetch_json(url: str):
    return _fetch(url, as_json=True)


# ─── Text normalisation ───────────────────────────────────────────────────────

def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _strip_html(html: str) -> str:
    """Convert FoundryVTT HTML description to clean plain text."""
    if not html:
        return ""
    # @UUID[...]{label} → label
    html = re.sub(r"@UUID\[[^\]]*\]\{([^}]+)\}", r"\1", html)
    # [[lookup @scale.class.feature]] — resolved before _strip_html via _resolve_scale_tokens;
    # this fallback catches any that slip through (e.g. no scale_tables loaded)
    html = re.sub(r"\[\[lookup\s+@scale\.[^\]]+\]\]", "(scales with level)", html)
    # [[/r ...]] inline roll expressions → strip entirely
    html = re.sub(r"\[\[/r\s+[^\]]+\]\]", "", html)
    # [[expr]]{label} foundry "rolled display" — keep the human-readable label,
    # drop the formula. Must run BEFORE the bare [[...]] strip below or the
    # label would be orphaned (e.g. "[[lookup @name]]{monster}" → "monster").
    html = re.sub(r"\[\[[^\]]*\]\]\{([^}]+)\}", r"\1", html)
    # Any remaining [[ ... ]] FoundryVTT tokens → strip
    html = re.sub(r"\[\[[^\]]*\]\]", "", html)
    # @Damage[...]{label} → label
    html = re.sub(r"@Damage\[[^\]]*\]\{([^}]+)\}", r"\1", html)
    # @Check[...]{label} → label
    html = re.sub(r"@Check\[[^\]]*\]\{([^}]+)\}", r"\1", html)
    # Any remaining @Token[...]{label} → label
    html = re.sub(r"@\w+\[[^\]]*\]\{([^}]+)\}", r"\1", html)
    # Any remaining bare @Token[...] → strip
    html = re.sub(r"@\w+\[[^\]]*\]", "", html)
    # Foundry token attribute fragments that leak after @UUID/[[lookup]] strip.
    # e.g. "@UUID[...]{Prone} apply=false condition" — @UUID strips to "Prone",
    # leaving " apply=false" hanging. Same for extended / average roll modifiers.
    html = re.sub(r"\s+\b(?:apply|extended|average)=\w+\b", "", html)
    # &amp;Reference[id]{label} → label (label is human-readable; id is lookup key)
    html = re.sub(r"&amp;Reference\[[^\]]+\]\{([^}]+)\}", r"\1", html)
    # &Reference[id]{label} → label (in case already decoded)
    html = re.sub(r"&Reference\[[^\]]+\]\{([^}]+)\}", r"\1", html)
    # &amp;Reference[Dash] without label → Dash (fallback for the unlabeled form)
    html = re.sub(r"&amp;Reference\[([^\]]+)\]", r"\1", html)
    # &Reference[Dash] without label → Dash (fallback for the unlabeled form)
    html = re.sub(r"&Reference\[([^\]]+)\]", r"\1", html)
    # Malformed upstream variant: &Reference[id} with curly closer instead of
    # square — observed on Adult/Ancient Black Dragon. Treat as unlabeled.
    html = re.sub(r"&(?:amp;)?Reference\[([^\]\}]+)\}", r"\1", html)
    # Orphan {Title-Cased Label} left after [[/item id]]{Label} resolution
    # produced "Label{Label}" or "Item Name (Form Only){Label}" duplicates.
    # Plain-text descriptions never legitimately use curly-braced title-cased
    # phrases in 5e SRD content, so strip these as artifacts.
    html = re.sub(r"\s*\{[A-Z][\w\s\-']*\}", "", html)
    # List items
    html = re.sub(r"<li[^>]*>", "• ", html)
    html = re.sub(r"</li>", "\n", html)
    # Paragraphs/divs as line breaks
    html = re.sub(r"</p>|</div>|<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    # Table cells (crude: separate with spaces)
    html = re.sub(r"<td[^>]*>|<th[^>]*>", "  ", html, flags=re.IGNORECASE)
    html = re.sub(r"</tr>", "\n", html, flags=re.IGNORECASE)
    # Strip all remaining tags
    html = re.sub(r"<[^>]+>", "", html)
    # HTML entities
    html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    # Collapse whitespace
    lines = [ln.strip() for ln in html.splitlines()]
    text  = "\n".join(ln for ln in lines if ln)
    text  = re.sub(r"\n{3,}", "\n\n", text).strip()
    # Punctuation artefacts from inline-token resolution leaving empties.
    # Mirror the cleanup _cleanup_action_prose does for action prose, applied
    # universally so spell/monster descriptions also come out clean.
    text = re.sub(r"[ \t]{2,}", " ", text)        # collapse internal multi-spaces
    text = re.sub(r":\s*\.\s*,?\s*", ": ", text)  # ": ." → ": "
    text = re.sub(r"\.\s*,", ",", text)           # ". ," → ","
    text = re.sub(r",\s*(?=,)", "", text)         # ", , " → ", "
    text = re.sub(r"\(\s*\)", "", text)           # "(  )" → ""
    text = re.sub(r"[ \t]{2,}", " ", text)        # final pass after substitutions
    # Strip FoundryVTT-specific "Foundry Note" sections (everything from that header onward)
    text = re.sub(r"\n?Foundry Note\b.*", "", text, flags=re.DOTALL).strip()
    return text


def _fmt_scale_table(table: dict) -> str:
    """Format {level_str: value_str} into a compact range string.
    e.g. {"1":"1d6","3":"2d6",...} → "1d6 (lvl 1–2), 2d6 (lvl 3–4), ..., 10d6 (lvl 19–20)"
    """
    levels = sorted(table.keys(), key=lambda x: int(x))
    parts  = []
    for i, lvl in enumerate(levels):
        val     = table[lvl]
        lvl_int = int(lvl)
        if i + 1 < len(levels):
            end = int(levels[i + 1]) - 1
            parts.append(f"{val} (lvl {lvl_int}–{end})" if end > lvl_int else f"{val} (lvl {lvl_int})")
        else:
            parts.append(f"{val} (lvl {lvl_int}–20)" if lvl_int < 20 else f"{val} (lvl 20)")
    return ", ".join(parts)


def _resolve_scale_tokens(html: str, scale_tables: dict) -> str:
    """Replace [[lookup @scale.class.identifier]] with formatted progression strings.
    Called before _strip_html so that the real data is embedded in the description.
    If a table is not found, substitutes '(scales with level)' as fallback.
    """
    if "[[" not in html:
        return html

    def _replacer(m):
        inner = re.search(r'@scale\.(\w+)\.([^\]\s]+)', m.group(0))
        if not inner:
            return "(scales with level)"
        cls_name   = inner.group(1)
        identifier = inner.group(2)
        table = (scale_tables.get(cls_name) or {}).get(identifier)
        return _fmt_scale_table(table) if table else "(scales with level)"

    return re.sub(r'\[\[lookup\s+@scale\.[^\]]+\]\]', _replacer, html)


def _join_desc(desc) -> str:
    """Normalise 5e-bits desc field (list or string) to a single string."""
    if isinstance(desc, list):
        return "\n\n".join(str(d) for d in desc)
    return str(desc) if desc else ""


# ─── FoundryVTT 2024 spell normaliser ─────────────────────────────────────────

# Foundry's spell schools use 3-letter codes; map to long form so the
# downstream consumer (lookup.py + display) doesn't need to know foundry
# encoding.
_FVTT_SCHOOL = {
    "abj": "Abjuration", "con": "Conjuration", "div": "Divination",
    "enc": "Enchantment", "evo": "Evocation", "ill": "Illusion",
    "nec": "Necromancy", "trs": "Transmutation",
}

# Foundry duration units; map to readable strings.
_FVTT_DUR = {
    "inst": "Instantaneous", "round": "round", "minute": "minute",
    "hour": "hour", "day": "day", "year": "year", "perm": "Permanent",
    "spec": "Special", "turn": "turn",
}

# Foundry activation types; map to readable strings.
_FVTT_ACT = {
    "action": "Action", "bonus": "Bonus Action", "reaction": "Reaction",
    "minute": "minute", "hour": "hour", "day": "day", "special": "Special",
    "ritual": "Ritual",
}

# Foundry "affects" target types map (system.target.affects.type) to their
# human-readable label form used inside spell descriptions. The 2024 SRD
# yamls embed [[lookup @labels.description.affects]] expecting these.
_FVTT_AFFECTS = {
    "creature":  "creature",
    "ally":      "ally",
    "enemy":     "enemy",
    "willing":   "willing creature",
    "object":    "object",
    "self":      "you",
    "space":     "space",
    "any":       "creature or object",
    "creatureOrObject": "creature or object",
}

# Foundry template shape codes (system.target.template.type) → readable noun.
_FVTT_SHAPE = {
    "sphere":   "sphere",
    "cube":     "cube",
    "cone":     "cone",
    "line":     "line",
    "cylinder": "cylinder",
    "radius":   "radius",
    "square":   "square",
    "wall":     "wall",
}


def _apply_token_modifier(text: str, modifier: str) -> str:
    """Apply a foundry lookup modifier (capitalize/lowercase/uppercase) to text."""
    if not text:
        return text
    if modifier == "capitalize":
        return text[:1].upper() + text[1:]
    if modifier == "lowercase":
        return text.lower()
    if modifier == "uppercase":
        return text.upper()
    return text


# Foundry ability/skill abbreviations used inside [[/check]] and [[/save]]
# tokens. Mapping to readable nouns so the prose reads naturally instead
# of leaving empty slots when the inline-roll token is stripped.
_FVTT_ABILITY = {
    "str": "Strength", "dex": "Dexterity", "con": "Constitution",
    "int": "Intelligence", "wis": "Wisdom", "cha": "Charisma",
}
_FVTT_SKILL = {
    "acr": "Acrobatics", "ani": "Animal Handling", "arc": "Arcana",
    "ath": "Athletics", "dec": "Deception", "his": "History",
    "ins": "Insight", "itm": "Intimidation", "inv": "Investigation",
    "med": "Medicine", "nat": "Nature", "prc": "Perception",
    "prf": "Performance", "per": "Persuasion", "rel": "Religion",
    "slt": "Sleight of Hand", "ste": "Stealth", "sur": "Survival",
}


def _kvargs(s: str) -> dict:
    """Parse foundry inline-roll arg blob 'ability=str skill=ath dc=20' into
    a dict. Tokens without '=' are kept under '_pos' (positional list).
    """
    out: dict = {"_pos": []}
    for tok in (s or "").strip().split():
        if "=" in tok:
            k, _, v = tok.partition("=")
            out[k.strip()] = v.strip()
        else:
            out["_pos"].append(tok)
    return out


def _format_dc(dc_raw: str) -> str:
    """Render a foundry dc= value as 'DC N' or '' if it references a formula."""
    if not dc_raw or dc_raw.startswith("@"):
        return ""
    # numeric or simple expression
    return f"DC {dc_raw}" if re.match(r"^\d+$", dc_raw) else ""


def _format_check_token(args: dict) -> str:
    """Render [[/check ability=X skill=Y dc=Z]] as readable prose.
    Examples:
      ability=str skill=ath dc=20      -> 'DC 20 Strength (Athletics)'
      ability=wis skill=prc            -> 'Wisdom (Perception)'
      ability=wis skill=prc dc=@...    -> 'Wisdom (Perception)'
    """
    ability = _FVTT_ABILITY.get((args.get("ability") or "").lower(), "")
    skill   = _FVTT_SKILL.get((args.get("skill") or "").lower(), "")
    dc      = _format_dc(args.get("dc", ""))
    if ability and skill:
        body = f"{ability} ({skill})"
    elif skill:
        body = skill
    elif ability:
        body = ability
    else:
        return ""
    return f"{dc} {body}".strip()


def _format_save_token(args: dict) -> str:
    """Render [[/save ability=X dc=Z format=long]] as readable prose.
    Examples:
      ability=cha dc=@... format=long  -> 'Charisma saving throw'
      ability=dex                       -> 'Dexterity saving throw'
      ability=str dc=15                 -> 'DC 15 Strength saving throw'
    """
    ability = _FVTT_ABILITY.get((args.get("ability") or "").lower(), "")
    if not ability:
        return ""
    dc = _format_dc(args.get("dc", ""))
    body = f"{ability} saving throw"
    return f"{dc} {body}".strip()


def _format_damage_token(args: dict) -> str:
    """Render [[/damage ...]] inline-roll for spell or actor-item prose.
    The token shape varies:
      [[/damage 50 type=bludgeoning]]   -> '50 bludgeoning'
      [[/damage 2d4 type=piercing]]     -> '2d4 piercing'
      [[/damage 2d8 type=fire]]         -> '2d8 fire'
      [[/damage 1d6 force]]             -> '1d6 force'
      [[/damage average extended]]      -> '' (actor-item: weapon's base damage
                                              already appears in the action
                                              prefix, so duplicating reads
                                              awkwardly; cleanup pass collapses)
      [[/damage]]                       -> '' (cleanup pass collapses)
    """
    pos = args.get("_pos") or []
    typed = (args.get("type") or "").strip().lower()
    if not pos:
        return ""
    first = pos[0].lower()
    # Pure dice or pure number
    if re.match(r"^\d+d\d+", first) or re.match(r"^\d+$", first):
        dice = pos[0]
        dmg_type = typed
        if not dmg_type:
            for tok in pos[1:]:
                if tok.lower() in ("average", "extended", "critical"):
                    continue
                if re.match(r"^[a-zA-Z]+$", tok):
                    dmg_type = tok.lower()
                    break
        return f"{dice} {dmg_type}".strip()
    # 'average'/'extended' flag → blank. Matches the pre-fix actor-item
    # behaviour and avoids duplicating weapon damage already in the prefix.
    return ""


def _format_roll_token(formula: str) -> str:
    """Render [[/r <formula>]] as a readable dice expression.
    Examples:
      '1d6'       -> '1d6'
      '1d10 * 10' -> '1d10 x 10'  (ascii multiplier; downstream uses no unicode)
    """
    if not formula:
        return ""
    text = formula.strip()
    # Drop any inline foundry options after a comma (e.g. ", flavor=foo")
    text = text.split(",")[0].strip()
    # Replace '*' with ' x ' for readability; collapse repeated spaces.
    text = re.sub(r"\s*\*\s*", " x ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _resolve_inline_rolls(html: str) -> str:
    """Resolve foundry inline-roll tokens that show up in BOTH spell and
    actor-item descriptions:
      [[/r <formula>]]           -> formula text
      [[/damage <args>]]         -> 'NdM type' / 'N type' / ''
      [[/check ability=...]]     -> 'DC N Ability (Skill)'
      [[/save ability=...]]      -> 'DC N Ability saving throw'
      [[/attack ...]]            -> '' (display-only roll)

    Tokens we cannot parse fall through to '' so the surrounding cleanup
    pass can collapse the punctuation cleanly. Idempotent: a second pass
    over the same string is a no-op.
    """
    if not html or "[[/" not in html:
        return html
    # /r — preserve formula text
    html = re.sub(
        r"\[\[/r\s+([^\]]+)\]\]",
        lambda m: _format_roll_token(m.group(1)),
        html,
    )
    # /damage — dice + type or blank
    html = re.sub(
        r"\[\[/damage(?:\s+([^\]]*))?\]\]",
        lambda m: _format_damage_token(_kvargs(m.group(1) or "")),
        html,
    )
    # /check — readable check phrase
    html = re.sub(
        r"\[\[/check\s+([^\]]+)\]\]",
        lambda m: _format_check_token(_kvargs(m.group(1))),
        html,
    )
    # /save — readable save phrase
    html = re.sub(
        r"\[\[/save\s+([^\]]+)\]\]",
        lambda m: _format_save_token(_kvargs(m.group(1))),
        html,
    )
    # /attack — purely a roll display, drop it
    html = re.sub(r"\[\[/attack(?:\s+[^\]]*)?\]\]", "", html)
    return html


def _resolve_lookup_activity_tokens(html: str, activities: dict) -> str:
    """Resolve `[[lookup @<dotted.path> activity=<id> [<modifier>]]]` tokens
    against an `activities` map (id -> activity dict). Used by both the
    spell-description path (`system.activities`) and the actor-item path
    (`item.system.activities`) so the resolver lives in one place.

    Tokens whose activity id isn't found in the map are left for the
    catchall strip in _strip_html.
    """
    if not html or "[[" not in html:
        return html
    activities = activities or {}

    def _replacer(m):
        dotted   = m.group(1)
        act_id   = m.group(2)
        modifier = m.group(3) or ""
        activity = activities.get(act_id) or {}
        return _resolve_activity_lookup(dotted, modifier, activity)

    return re.sub(
        r"\[\[lookup\s+@([A-Za-z0-9_.]+)\s+activity=([A-Za-z0-9_]+)(?:\s+(\w+))?\]\]",
        _replacer,
        html,
    )


def _resolve_label_tokens(html: str, system: dict) -> str:
    """Replace [[lookup @labels.<path>]] tokens in foundry spell HTML with
    values derived from the spell's `system` dict.

    Also resolves `[[lookup @item.level]]` to the spell's level field, and
    the `[[<formula>]]{<label>}` rolled-display pattern (we keep just the
    label, since we don't fully evaluate the formula here).

    Runs BEFORE _strip_html so resolved text is embedded in the description.
    Tokens we cannot resolve (no matching field, or empty) are left as
    `[[lookup @labels...]]` and stripped cleanly by _strip_html's catchall.
    """
    if not html or "[[" not in html:
        return html

    # First: resolve `[[lookup @item.level]]` and `[[lookup @item.level
    # <modifier>]]` to the spell's numeric level so the surrounding label
    # text reads correctly (e.g. "Level 1 darts").
    spell_level = system.get("level")
    if spell_level is not None:
        def _item_level_replacer(m):
            modifier = m.group(1) or ""
            return _apply_token_modifier(str(spell_level), modifier)
        html = re.sub(
            r"\[\[lookup\s+@item\.level(?:\s+(\w+))?\]\]",
            _item_level_replacer,
            html,
        )

    # Foundry's "rolled display" pattern: [[<formula>]]{<label>}. The label
    # is meant as fallback text for when the formula can't be evaluated; in
    # Foundry it would render as the rolled value. When this token sits as
    # the *only* content of its paragraph (e.g. magic-missile's trailing
    # "Level 1 darts" scaling-display), the surrounding sentence context is
    # missing, so we drop the entire <p>…</p> — the rules text that
    # precedes it already explains the scaling. Inline rolled-displays
    # (with surrounding prose in the same paragraph) keep their label.
    html = re.sub(
        r"<p[^>]*>\s*\[\[(?!lookup\b|/)[^\]]+\]\]\{[^}]*\}\s*</p>",
        "",
        html,
        flags=re.IGNORECASE,
    )
    # Inline rolled-display: keep the label, drop the formula braces. Capture
    # is non-greedy so adjacent patterns don't collapse into one match.
    html = re.sub(
        r"\[\[(?!lookup\b|/)[^\]]+\]\]\{([^}]*)\}",
        lambda m: m.group(1),
        html,
    )

    target   = (system.get("target", {}) or {})
    affects  = (target.get("affects", {}) or {})
    template = (target.get("template", {}) or {})
    activation = (system.get("activation", {}) or {})
    duration   = (system.get("duration", {}) or {})
    rng        = (system.get("range", {}) or {})

    def _affects_label() -> str:
        atype = (affects.get("type") or "").strip()
        count_raw = affects.get("count")
        try:
            count = int(count_raw) if count_raw not in (None, "", 0) else None
        except (TypeError, ValueError):
            count = None
        base = _FVTT_AFFECTS.get(atype, atype)
        if not base:
            # No affects type defined — fall back to "creature" for sane prose.
            base = "creature"
        if count and count > 1:
            # naive plural: append 's' if not already
            plural = base if base.endswith("s") else base + "s"
            return f"{count} {plural}"
        if count == 1:
            return f"{count} {base}"
        # No count specified → plural form (e.g. "creatures in a sphere")
        return base if base.endswith("s") else base + "s"

    def _template_label() -> str:
        shape = (template.get("type") or "").strip()
        size  = template.get("size")
        width = template.get("width")
        height = template.get("height")
        units = (template.get("units") or "ft").strip() or "ft"
        unit_word = "foot" if units in ("ft", "feet", "foot") else units
        if not shape:
            return ""
        shape_word = _FVTT_SHAPE.get(shape, shape)
        # Special shapes
        if shape == "sphere" and size:
            return f"{size}-{unit_word}-radius {shape_word}"
        if shape == "cylinder" and size:
            if height:
                return f"{size}-{unit_word}-radius, {height}-{unit_word}-tall {shape_word}"
            return f"{size}-{unit_word}-radius {shape_word}"
        if shape == "line" and size:
            if width:
                return f"{size}-{unit_word}-long, {width}-{unit_word}-wide {shape_word}"
            return f"{size}-{unit_word}-long {shape_word}"
        if shape in ("cone", "cube", "square") and size:
            return f"{size}-{unit_word} {shape_word}"
        if shape == "radius" and size:
            return f"{size}-{unit_word} {shape_word}"
        if size:
            return f"{size}-{unit_word} {shape_word}"
        return shape_word

    def _activation_label() -> str:
        atype = activation.get("type") or ""
        aval  = activation.get("value")
        readable = _FVTT_ACT.get(atype, atype.capitalize() if atype else "")
        if not readable:
            return ""
        if aval:
            return f"{aval} {readable}"
        # Single-action defaults — "1 Action", "1 Bonus Action", "1 Reaction"
        if atype in ("action", "bonus", "reaction"):
            return f"1 {readable}"
        return readable

    def _duration_label() -> str:
        units = duration.get("units") or ""
        val   = duration.get("value")
        readable_unit = _FVTT_DUR.get(units, units)
        if units == "inst":
            return "Instantaneous"
        if units in ("perm",):
            return "Permanent"
        if units in ("spec",):
            return "Special"
        concentration = "concentration" in (system.get("properties") or [])
        if val and readable_unit:
            unit_str = readable_unit
            try:
                if int(val) != 1 and not unit_str.endswith("s"):
                    unit_str = unit_str + "s"
            except (TypeError, ValueError):
                pass
            base = f"{val} {unit_str}"
            if concentration:
                return f"Concentration, up to {base}"
            return base
        if readable_unit:
            return readable_unit
        return ""

    def _range_label() -> str:
        units = (rng.get("units") or "").strip()
        val   = rng.get("value")
        special = (rng.get("special") or "").strip()
        if units == "self":
            return "Self"
        if units == "touch":
            return "Touch"
        if units == "any":
            return "Any"
        if units == "spec":
            return special or "Special"
        if val and units:
            unit_word = "feet" if units in ("ft", "feet") else units
            return f"{val} {unit_word}"
        if special:
            return special
        if units:
            return units
        return ""

    resolvers = {
        "description.affects":  _affects_label,
        "description.template": _template_label,
        "area.shape":           _template_label,
        "activation":           _activation_label,
        "duration":             _duration_label,
        "range":                _range_label,
    }

    pattern = re.compile(r"\[\[lookup\s+@labels\.([a-zA-Z0-9_.]+)(?:\s+(\w+))?\]\]")

    def _replacer(m):
        key      = m.group(1)
        modifier = m.group(2) or ""
        fn = resolvers.get(key)
        if not fn:
            return m.group(0)  # leave untouched; _strip_html will erase it
        try:
            value = fn() or ""
        except Exception:
            return m.group(0)
        if not value:
            return m.group(0)
        return _apply_token_modifier(value, modifier)

    return pattern.sub(_replacer, html)


def _norm_fvtt_spell_2024(doc: dict, path: str) -> dict | None:
    """Normalise a foundryvtt/dnd5e packs/_source/spells24/.../<spell>.yml
    document into the same shape used by lookup.py / the display.

    Returns None for malformed/empty documents. Output schema mirrors the
    5e-bits _norm_spell shape so downstream code is rule-agnostic.
    """
    if not isinstance(doc, dict):
        return None
    name = doc.get("name", "").strip()
    if not name:
        return None
    sys_data = doc.get("system", {}) or {}
    src      = sys_data.get("source", {}) or {}

    desc_html = (sys_data.get("description", {}) or {}).get("value", "") or ""
    desc_html = _resolve_label_tokens(desc_html, sys_data)
    desc_html = _resolve_lookup_activity_tokens(
        desc_html, sys_data.get("activities", {}) or {},
    )
    desc_html = _resolve_inline_rolls(desc_html)
    description = _cleanup_action_prose(_strip_html(desc_html).strip())

    level = sys_data.get("level")
    try:
        level = int(level) if level is not None else None
    except (TypeError, ValueError):
        level = None

    school_code = sys_data.get("school", "") or ""
    school = _FVTT_SCHOOL.get(school_code, school_code.capitalize() if school_code else "")

    activation = sys_data.get("activation", {}) or {}
    act_type = _FVTT_ACT.get(activation.get("type"), activation.get("type") or "")
    act_val  = activation.get("value")
    casting_time = (f"{act_val} {act_type}" if act_val and act_type
                    else act_type or "")

    duration = sys_data.get("duration", {}) or {}
    dur_units = _FVTT_DUR.get(duration.get("units"), duration.get("units") or "")
    dur_val   = duration.get("value")
    duration_str = (f"{dur_val} {dur_units}" if dur_val and dur_units
                    else dur_units or "")

    rng = sys_data.get("range", {}) or {}
    rng_val = rng.get("value")
    rng_units = rng.get("units", "") or ""
    range_str = (f"{rng_val} {rng_units}" if rng_val else
                 (rng.get("special") or rng_units))

    properties = sys_data.get("properties", []) or []
    components_short = []
    for p in properties:
        if p == "vocal":    components_short.append("V")
        elif p == "somatic": components_short.append("S")
        elif p == "material": components_short.append("M")
    materials = (sys_data.get("materials", {}) or {}).get("value", "") or ""

    ritual = "ritual" in properties
    concentration = "concentration" in properties

    return {
        "name":          name,
        "index":         _slugify(name),
        "description":   description,
        "level":         level if level is not None else 0,
        "school":        school,
        "casting_time":  casting_time,
        "duration":      duration_str,
        "range":         str(range_str).strip() if range_str else "",
        # Stored as a list (matching 2014 5e-bits schema) so lookup.py's
        # ", ".join(...) formatter doesn't iterate the characters of a string.
        "components":    [c for c in components_short if c],
        "material":      materials,
        "ritual":        ritual,
        "concentration": concentration,
        "_source":       "foundryvtt-2024",
        "_source_path":  path,
        "_license":      src.get("license", "CC-BY-4.0"),
        "_rules":        src.get("rules", "2024"),
    }


def _build_fvtt_spells_2024() -> list[dict]:
    """Walk packs/_source/spells24/ via the foundry repo tree, fetch each
    yaml, normalise into spell records.

    Honors the existing FVTT_TREE endpoint (already used for class features)
    so we don't double-fetch the tree. Returns empty list if PyYAML is
    missing — caller must handle.
    """
    if yaml is None:
        print("  foundry[spells24]  skipped (PyYAML not installed)")
        return []

    print("  foundry[spells24]  enumerating tree …", end="", flush=True)
    tree_data = _fetch_json(FVTT_TREE)
    if not tree_data:
        print(" failed")
        return []
    tree = tree_data.get("tree", [])

    spell_paths = [
        t["path"] for t in tree
        if t.get("path", "").startswith(f"{FVTT_SPELLS_2024_PATH}/")
        and t.get("path", "").endswith(".yml")
        and not os.path.basename(t["path"]).startswith("_")
    ]
    print(f" {len(spell_paths)} files")

    spells: list[dict] = []
    failed = 0
    for i, path in enumerate(spell_paths, 1):
        raw = _fetch(f"{RAW_FVTT}/{path}")
        if raw is None:
            failed += 1
            continue
        try:
            doc = yaml.safe_load(raw)
        except Exception:
            failed += 1
            continue
        rec = _norm_fvtt_spell_2024(doc, path)
        if rec:
            spells.append(rec)
        if i % 50 == 0:
            time.sleep(0.5)
            print(f"    … {i}/{len(spell_paths)}")

    print(f"  foundry[spells24]  {len(spells)} spells "
          f"({failed} failed/empty)")
    return spells


# ─── FoundryVTT 2024 monster normaliser ───────────────────────────────────────

# Foundry size codes → readable size names.
_FVTT_SIZE = {
    "tiny": "Tiny", "sm": "Small", "med": "Medium",
    "lg": "Large", "huge": "Huge", "grg": "Gargantuan",
}

# Foundry "category" keys for actor items that carry NPC actions/abilities.
# `feat` items are typically traits (special abilities). `weapon` items are
# attack actions. `equipment` items are gear (skipped in description).
_FVTT_ACTION_TYPES = {"feat", "weapon"}


def _cr_human(cr) -> str:
    """Convert numeric CR to the canonical fractional/whole-number string."""
    try:
        cr_f = float(cr)
    except (TypeError, ValueError):
        return str(cr) if cr else "?"
    if cr_f == 0:
        return "0"
    if cr_f == 0.125:
        return "1/8"
    if cr_f == 0.25:
        return "1/4"
    if cr_f == 0.5:
        return "1/2"
    if cr_f.is_integer():
        return str(int(cr_f))
    return str(cr_f)


def _proficiency_bonus_for_cr(cr_value: float) -> int:
    """Approximate proficiency bonus from CR (5e tables)."""
    if cr_value < 5:
        return 2
    if cr_value < 9:
        return 3
    if cr_value < 13:
        return 4
    if cr_value < 17:
        return 5
    if cr_value < 21:
        return 6
    if cr_value < 25:
        return 7
    if cr_value < 29:
        return 8
    return 9


def _format_movement(mv: dict) -> str:
    """Format the foundry movement dict (walk/fly/swim/etc.) → '30 ft, fly 60 ft'."""
    if not isinstance(mv, dict):
        return ""
    units = mv.get("units") or "ft"
    parts = []
    walk = mv.get("walk")
    if walk not in (None, "", 0):
        parts.append(f"{walk} {units}")
    for key in ("burrow", "climb", "fly", "swim"):
        v = mv.get(key)
        if v not in (None, "", 0):
            parts.append(f"{key} {v} {units}")
    if mv.get("hover"):
        parts.append("hover")
    return ", ".join(parts)


def _format_senses(senses: dict) -> str:
    """Foundry senses dict → 'darkvision 60 ft, passive Perception 10' style."""
    if not isinstance(senses, dict):
        return ""
    parts = []
    units = senses.get("units") or "ft"
    ranges = senses.get("ranges") or {}
    for key, v in ranges.items():
        if v in (None, "", 0):
            continue
        parts.append(f"{key} {v} {units}")
    special = (senses.get("special") or "").strip()
    if special:
        parts.append(special)
    return ", ".join(parts)


def _format_languages(langs: dict) -> str:
    """Foundry languages dict → comma-separated list."""
    if not isinstance(langs, dict):
        return ""
    vals = langs.get("value") or []
    custom = (langs.get("custom") or "").strip()
    pieces = [str(v).capitalize() for v in vals if v]
    if custom:
        pieces.append(custom)
    return ", ".join(pieces)


def _weapon_base_damage(item: dict) -> str:
    """Return a 'NdM type' summary for a weapon item, or '' if unavailable."""
    sd = item.get("system", {}) or {}
    base = (sd.get("damage", {}) or {}).get("base", {}) or {}
    n     = base.get("number")
    d     = base.get("denomination")
    bonus = base.get("bonus")
    types = base.get("types") or []
    if not (n and d):
        return ""
    dmg = f"{n}d{d}"
    if bonus:
        dmg += f"+{bonus}"
    if types:
        dmg += " " + "/".join(str(t) for t in types)
    return dmg


def _resolve_activity_lookup(path: str, modifier: str,
                              activity: dict) -> str:
    """Resolve `[[lookup @<dotted.path> activity=<id>]]` against a single
    activity dict. Returns the readable string, or '' if unresolvable.

    Used for monster reaction/utility activities (e.g. goblin-boss's
    Redirect Attack) where a feat description references its own activity's
    range / target / affects fields by dotted path.
    """
    if not isinstance(activity, dict):
        return ""
    # Walk the dotted path. We only support the prefixes that show up in
    # foundry actor item descriptions; anything else returns ''.
    parts = path.split(".")
    cur = activity
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return ""
    if cur is None:
        return ""
    text = str(cur).strip()
    if not text:
        return ""
    # If the resolved value is a foundry "type" code (creature/ally/enemy/…)
    # map it to readable English so prose reads naturally.
    if path.endswith("affects.type") and text in _FVTT_AFFECTS:
        text = _FVTT_AFFECTS[text]
    return _apply_token_modifier(text, modifier)


def _resolve_actor_item_tokens(html: str, *, actor_name: str,
                                item_name_by_id: dict, base_dmg: str,
                                activities: dict | None = None) -> str:
    """Resolve foundry inline tokens specific to actor item descriptions.

    Handles:
      [[lookup @name <modifier>]]                   → actor_name (with case modifier)
      [[lookup @<path> activity=<id> <modifier>]]   → activity field value
      [[/item .<id>]]                               → referenced item's name
      [[/r ...]] / [[/damage ...]] / [[/check ...]] / [[/save ...]]
                                                    → see _resolve_inline_rolls
      [[/attack ...]]                               → '' (cleanup pass collapses surroundings)
    Other [[...]] tokens are left for _strip_html's catchall.
    """
    if not html or "[[" not in html:
        return html

    activities = activities or {}

    # @name resolves to the actor's name. Foundry uses this so the same
    # template can be reused across renamed monsters; without resolution
    # the trailing literal `{monster}` leaks through.
    def _name_replacer(m):
        modifier = m.group(1) or ""
        return _apply_token_modifier(actor_name, modifier)
    html = re.sub(
        r"\[\[lookup\s+@name(?:\s+(\w+))?\]\]\{[^}]*\}",
        _name_replacer,
        html,
    )
    html = re.sub(
        r"\[\[lookup\s+@name(?:\s+(\w+))?\]\]",
        _name_replacer,
        html,
    )

    # [[lookup @<dotted.path> activity=<id> [<modifier>]]] — references a
    # field on one of the item's own activities. Used by reactions/utility
    # actions (e.g. goblin-boss Redirect Attack pulling its own range and
    # affects.special into the prose). Shared with the spell path so both
    # call the same resolver.
    html = _resolve_lookup_activity_tokens(html, activities)

    # [[/item .<id>]] → that item's name.
    def _item_replacer(m):
        ref_id = m.group(1)
        return item_name_by_id.get(ref_id, "")
    html = re.sub(
        r"\[\[/item\s+\.([A-Za-z0-9_]+)\]\]",
        _item_replacer,
        html,
    )

    # [[/r ...]], [[/damage ...]], [[/check ...]], [[/save ...]], [[/attack ...]]
    # — shared with the spell path. `base_dmg` is unused here (the weapon's
    # base damage already appears in the action prefix; duplicating inline
    # reads awkwardly), but kept as a parameter for future contexts.
    _ = base_dmg
    html = _resolve_inline_rolls(html)

    return html


def _cleanup_action_prose(text: str) -> str:
    """Collapse the punctuation artefacts left when an inline-roll token
    resolved to an empty string. Targets patterns like
    ': . ,', '( )', ', ,', '  ', and 'plus  damage'.
    """
    if not text:
        return text
    # Repeated whitespace inside a line.
    text = re.sub(r"[ \t]{2,}", " ", text)
    # ", , " and ", ," → ", "
    text = re.sub(r",\s*(?=,)", "", text)
    # ". . " (period + space + period + space, possibly repeated) → ". "
    # Produced when an inline-roll token resolves to empty between two
    # sentences: "reach 5 ft. [[/damage]]. If the target..." → "reach 5 ft. . If".
    text = re.sub(r"\.\s+(\.\s+)+", ". ", text)
    # Empty parens "(  )" → ""
    text = re.sub(r"\(\s*\)", "", text)
    # ": ." (colon followed by stray period) → ":"
    text = re.sub(r":\s*\.\s*,?\s*", ": ", text)
    # ". ," → ","
    text = re.sub(r"\.\s*,", ",", text)
    # "plus  damage" / "using  or  in" — collapse the doubled space again.
    text = re.sub(r"[ \t]{2,}", " ", text)
    # Leading punctuation on a line — handle repeats like ". . If" produced
    # when two adjacent inline-roll tokens both resolve to empty.
    text = re.sub(r"^\s*([,.]+\s*)+", "", text, flags=re.MULTILINE)
    # Trailing whitespace
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    # Final pass: collapse "  " once more.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.strip()
    # If everything that survived is bare punctuation/whitespace (e.g. just
    # "." or ". ." left after both [[/attack]] and [[/damage]] resolved to
    # empty), treat as no description so the caller can drop the trailing
    # ": " prefix.
    if text and not re.search(r"[A-Za-z0-9]", text):
        return ""
    return text


def _format_item_action(item: dict, *, actor_name: str = "",
                        item_name_by_id: dict | None = None) -> str:
    """Render a foundry actor item (feat/weapon) as a description line.

    `actor_name` and `item_name_by_id` provide context so foundry tokens
    like [[lookup @name]] and [[/item .<id>]] resolve to real values
    instead of leaving empty slots in the output.
    """
    if not isinstance(item, dict):
        return ""
    name = (item.get("name") or "").strip()
    if not name:
        return ""
    sd = item.get("system", {}) or {}
    desc_html = (sd.get("description", {}) or {}).get("value", "") or ""
    itype = item.get("type", "")
    base_dmg = _weapon_base_damage(item) if itype == "weapon" else ""
    activities = sd.get("activities", {}) or {}
    desc_html = _resolve_actor_item_tokens(
        desc_html,
        actor_name=actor_name,
        item_name_by_id=item_name_by_id or {},
        base_dmg=base_dmg,
        activities=activities if isinstance(activities, dict) else {},
    )
    desc = _cleanup_action_prose(_strip_html(desc_html).strip())
    if itype == "weapon":
        prefix = f"Action — {name}"
        if base_dmg:
            prefix += f" ({base_dmg})"
        return f"{prefix}: {desc}" if desc else prefix
    # feat / trait
    return f"{name}: {desc}" if desc else name


def _norm_fvtt_monster_2024(doc: dict, path: str) -> dict | None:
    """Normalise a foundryvtt/dnd5e packs/_source/actors24/.../<actor>.yml
    document into the same shape as _norm_monster (5e-bits 2014).

    Returns None for malformed/non-NPC documents.
    """
    if not isinstance(doc, dict):
        return None
    if doc.get("type") != "npc":
        return None
    name = (doc.get("name") or "").strip()
    if not name:
        return None
    sys_data = doc.get("system", {}) or {}
    src      = sys_data.get("source", {}) or {}
    details  = sys_data.get("details", {}) or {}
    attrs    = sys_data.get("attributes", {}) or {}
    traits   = sys_data.get("traits", {}) or {}
    abilities = sys_data.get("abilities", {}) or {}

    cr_raw = details.get("cr")
    try:
        cr_value = float(cr_raw) if cr_raw is not None else 0.0
    except (TypeError, ValueError):
        cr_value = 0.0

    hp_block = attrs.get("hp", {}) or {}
    hp_max   = hp_block.get("max") or hp_block.get("value")
    hp_dice  = hp_block.get("formula", "") or ""

    ac_block = attrs.get("ac", {}) or {}
    ac_val   = ac_block.get("flat")
    if ac_val is None:
        ac_val = ac_block.get("value")
    if ac_val is None:
        # Look for an armor item among the actor's gear; foundry computes
        # AC at runtime from `armor.value` + DEX mod when ac.calc=default.
        armor_base = None
        for item in (doc.get("items") or []):
            if not isinstance(item, dict):
                continue
            sd_i = item.get("system", {}) or {}
            armor = (sd_i.get("armor") or {})
            if armor.get("value") is not None:
                armor_base = armor.get("value")
                break
        # Compute DEX modifier
        try:
            dex_mod = (int((abilities.get("dex", {}) or {}).get("value", 10)) - 10) // 2
        except (TypeError, ValueError):
            dex_mod = 0
        if armor_base is not None:
            try:
                ac_val = int(armor_base) + dex_mod
            except (TypeError, ValueError):
                ac_val = "?"
        else:
            # Natural armor default: 10 + DEX mod
            ac_val = 10 + dex_mod

    speed_str = _format_movement(attrs.get("movement", {}) or {})
    senses_str = _format_senses(attrs.get("senses", {}) or {})
    langs_str  = _format_languages(traits.get("languages", {}) or {})

    size_code = traits.get("size", "")
    size = _FVTT_SIZE.get(size_code, size_code or "")

    type_block = details.get("type", {}) or {}
    if isinstance(type_block, dict):
        m_type = (type_block.get("value") or "").strip()
        subtype = (type_block.get("subtype") or "").strip()
        if subtype:
            m_type = f"{m_type} ({subtype})" if m_type else subtype
    else:
        m_type = str(type_block)

    # Ability scores
    def _ab(key):
        v = (abilities.get(key, {}) or {}).get("value")
        try:
            return int(v) if v is not None else 10
        except (TypeError, ValueError):
            return 10

    str_v, dex_v, con_v = _ab("str"), _ab("dex"), _ab("con")
    int_v, wis_v, cha_v = _ab("int"), _ab("wis"), _ab("cha")

    alignment = (details.get("alignment") or "").strip()

    # Description: biography text + flattened items
    bio_html = ((details.get("biography") or {}).get("value")
                if isinstance(details.get("biography"), dict) else "")
    bio_txt  = _strip_html(bio_html or "").strip()

    # Build a foundry-id → item-name map so [[/item .<id>]] tokens in
    # action descriptions (e.g. Multiattack referencing the actor's own
    # weapons) can resolve to readable names.
    item_name_by_id: dict = {}
    for item in (doc.get("items") or []):
        if isinstance(item, dict):
            iid = item.get("_id") or ""
            iname = (item.get("name") or "").strip()
            if iid and iname:
                item_name_by_id[iid] = iname

    parts = []
    if bio_txt:
        parts.append(bio_txt)
    for item in (doc.get("items") or []):
        if not isinstance(item, dict):
            continue
        if item.get("type") not in _FVTT_ACTION_TYPES:
            continue
        line = _format_item_action(
            item,
            actor_name=name,
            item_name_by_id=item_name_by_id,
        )
        if line:
            parts.append(line)
    description = "\n\n".join(parts)

    # XP table approximation by CR (used only when xp not in source).
    XP_BY_CR = {
        0: 10, 0.125: 25, 0.25: 50, 0.5: 100, 1: 200, 2: 450, 3: 700,
        4: 1100, 5: 1800, 6: 2300, 7: 2900, 8: 3900, 9: 5000, 10: 5900,
        11: 7200, 12: 8400, 13: 10000, 14: 11500, 15: 13000, 16: 15000,
        17: 18000, 18: 20000, 19: 22000, 20: 25000, 21: 33000, 22: 41000,
        23: 50000, 24: 62000, 25: 75000, 26: 90000, 27: 105000, 28: 120000,
        29: 135000, 30: 155000,
    }
    xp_val = XP_BY_CR.get(cr_value, "?")

    return {
        "name":  name,
        "index": _slugify(name),
        "description": description,
        "cr":    _cr_human(cr_value),
        "cr_value": cr_value,
        "xp":    xp_val,
        "size":  size,
        "type":  m_type,
        "hp":    hp_max if hp_max is not None else "?",
        "hp_dice": hp_dice,
        "ac":    ac_val if ac_val is not None else "?",
        "speed": speed_str,
        "str":   str_v,
        "dex":   dex_v,
        "con":   con_v,
        "int":   int_v,
        "wis":   wis_v,
        "cha":   cha_v,
        "alignment": alignment,
        "languages": langs_str,
        "senses":    senses_str,
        "proficiency_bonus": _proficiency_bonus_for_cr(cr_value),
        "_source":      "foundryvtt-2024",
        "_source_path": path,
        "_license":     src.get("license", "CC-BY-4.0"),
        "_rules":       src.get("rules", "2024"),
    }


def _build_fvtt_monsters_2024() -> list[dict]:
    """Walk packs/_source/actors24/ via the foundry repo tree, fetch each
    yaml, normalise into monster records.

    Returns empty list if PyYAML is missing — caller handles fallback.
    """
    if yaml is None:
        print("  foundry[actors24]  skipped (PyYAML not installed)")
        return []

    print("  foundry[actors24]  enumerating tree …", end="", flush=True)
    tree_data = _fetch_json(FVTT_TREE)
    if not tree_data:
        print(" failed")
        return []
    tree = tree_data.get("tree", [])

    actor_paths = [
        t["path"] for t in tree
        if t.get("path", "").startswith(f"{FVTT_ACTORS_2024_PATH}/")
        and t.get("path", "").endswith(".yml")
        and not os.path.basename(t["path"]).startswith("_")
    ]
    print(f" {len(actor_paths)} files")

    monsters: list[dict] = []
    failed = 0
    skipped_non_npc = 0
    for i, path in enumerate(actor_paths, 1):
        raw = _fetch(f"{RAW_FVTT}/{path}")
        if raw is None:
            failed += 1
            continue
        try:
            doc = yaml.safe_load(raw)
        except Exception:
            failed += 1
            continue
        rec = _norm_fvtt_monster_2024(doc, path)
        if rec:
            monsters.append(rec)
        else:
            skipped_non_npc += 1
        if i % 50 == 0:
            time.sleep(0.5)
            print(f"    … {i}/{len(actor_paths)}")

    print(f"  foundry[actors24]  {len(monsters)} monsters "
          f"({failed} failed, {skipped_non_npc} non-npc/empty)")
    return monsters


# ─── 5e-bits normalisers ──────────────────────────────────────────────────────

def _norm_spell(r: dict) -> dict:
    school = r.get("school", {})
    return {
        "name":         r.get("name", ""),
        "index":        r.get("index", _slugify(r.get("name", ""))),
        "description":  _join_desc(r.get("desc", [])),
        "higher_level": _join_desc(r.get("higher_level", [])),
        "level":        r.get("level", 0),
        "school":       school.get("name", school) if isinstance(school, dict) else str(school),
        "casting_time": r.get("casting_time", ""),
        "range":        r.get("range", ""),
        "components":   r.get("components", []),
        "material":     r.get("material", ""),
        "duration":     r.get("duration", ""),
        "concentration":r.get("concentration", False),
        "ritual":       r.get("ritual", False),
        "classes":      [c.get("name", c) if isinstance(c, dict) else str(c)
                         for c in r.get("classes", [])],
    }


def _norm_equipment(r: dict) -> dict:
    cat  = r.get("equipment_category", {})
    cost = r.get("cost", {})
    dmg  = r.get("damage", {})
    dmg2 = r.get("two_handed_damage", {})
    rng  = r.get("range", {})
    trng = r.get("throw_range", {})
    ac   = r.get("armor_class", {})
    props = [p.get("name", p) if isinstance(p, dict) else str(p)
             for p in r.get("properties", [])]
    return {
        "name":          r.get("name", ""),
        "index":         r.get("index", _slugify(r.get("name", ""))),
        "description":   _join_desc(r.get("desc", [])),
        "category":      cat.get("name", "") if isinstance(cat, dict) else str(cat),
        "cost":          f"{cost.get('quantity','?')} {cost.get('unit','?')}"
                         if isinstance(cost, dict) else "",
        "weight":        r.get("weight"),
        "damage":        f"{dmg.get('damage_dice','')} {dmg.get('damage_type',{}).get('name','')}"
                         .strip() if dmg else "",
        "damage_2h":     f"{dmg2.get('damage_dice','')} {dmg2.get('damage_type',{}).get('name','')}"
                         .strip() if dmg2 else "",
        "ac":            f"AC {ac.get('base','')} + DEX" if ac else "",
        "properties":    props,
        "range":         f"{rng.get('normal','?')}/{rng.get('long','?')} ft"
                         if rng and rng.get("normal") else "",
        "throw_range":   f"{trng.get('normal','?')}/{trng.get('long','?')} ft" if trng else "",
        "stealth_disadv":r.get("stealth_disadvantage", False),
        "str_minimum":   r.get("str_minimum"),
    }


def _norm_magic_item(r: dict) -> dict:
    rar = r.get("rarity", {})
    cat = r.get("equipment_category", {})
    return {
        "name":        r.get("name", ""),
        "index":       r.get("index", _slugify(r.get("name", ""))),
        "description": _join_desc(r.get("desc", [])),
        "rarity":      rar.get("name", rar) if isinstance(rar, dict) else str(rar),
        "category":    cat.get("name", "") if isinstance(cat, dict) else str(cat),
        "attunement":  "attunement" in _join_desc(r.get("desc", [])).lower(),
    }


def _norm_condition(r: dict) -> dict:
    # 2014 schema uses `desc` (list); 2024 uses `description` (string).
    # Fall back to whichever is present so both rulesets render correctly.
    desc = _join_desc(r.get("desc", []))
    if not desc:
        desc = str(r.get("description", "") or "")
    return {
        "name":        r.get("name", ""),
        "index":       r.get("index", _slugify(r.get("name", ""))),
        "description": desc,
    }


def _norm_monster(r: dict) -> dict:
    ac_list = r.get("armor_class", [])
    ac_val  = (ac_list[0].get("value") if isinstance(ac_list, list) and ac_list
               and isinstance(ac_list[0], dict) else
               ac_list[0] if isinstance(ac_list, list) and ac_list else
               ac_list if isinstance(ac_list, (int, float)) else "?")
    speed   = r.get("speed", {})
    speed_s = ", ".join(f"{k} {v}" for k, v in speed.items() if v) if isinstance(speed, dict) else ""
    # Flatten special abilities + actions into description
    parts = []
    for sa in r.get("special_abilities", []):
        parts.append(f"{sa.get('name','')}: {sa.get('desc','')}")
    for a in r.get("actions", []):
        parts.append(f"Action — {a.get('name','')}: {a.get('desc','')}")
    for a in r.get("legendary_actions", []):
        parts.append(f"Legendary — {a.get('name','')}: {a.get('desc','')}")
    return {
        "name":  r.get("name", ""),
        "index": r.get("index", _slugify(r.get("name", ""))),
        "description": "\n\n".join(parts),
        "cr":    r.get("challenge_rating", "?"),
        "xp":    r.get("xp", "?"),
        "size":  r.get("size", ""),
        "type":  r.get("type", ""),
        "hp":    r.get("hit_points", "?"),
        "hp_dice": r.get("hit_dice", ""),
        "ac":    ac_val,
        "speed": speed_s,
        "str":   r.get("strength", 10),
        "dex":   r.get("dexterity", 10),
        "con":   r.get("constitution", 10),
        "int":   r.get("intelligence", 10),
        "wis":   r.get("wisdom", 10),
        "cha":   r.get("charisma", 10),
        "alignment": r.get("alignment", ""),
        "languages": r.get("languages", ""),
    }


# ─── FoundryVTT normaliser ────────────────────────────────────────────────────

def _parse_scale_tables(class_doc: dict) -> dict:
    """Extract ScaleValue advancements from a class YAML document.
    Returns {identifier: {level_str: value_str}}
    Indexed by both config.identifier and _slugify(title) so either lookup hits.
    """
    tables = {}
    system = class_doc.get("system", {}) if "system" in class_doc else class_doc
    for adv in system.get("advancement", []):
        # Defensive: upstream advancement entries can be strings (UUID
        # references) instead of dicts in some 2024-era class docs.
        # Skip non-dict entries cleanly rather than crashing.
        if not isinstance(adv, dict):
            continue
        if adv.get("type") != "ScaleValue":
            continue
        title  = adv.get("title", "").strip()
        config = adv.get("configuration", {}) or {}
        scale  = config.get("scale", {})
        vtype  = config.get("type", "dice")
        ident  = (config.get("identifier") or "").strip() or _slugify(title)
        if not scale or not title:
            continue

        table = {}
        for lvl, val in scale.items():
            if not isinstance(val, dict):
                continue
            if vtype == "dice":
                n, f = val.get("number", 0), val.get("faces", 0)
                if n and f:
                    table[str(lvl)] = f"{n}d{f}"
            elif vtype == "number":
                v = val.get("value")
                if v is not None:
                    table[str(lvl)] = f"+{v}" if isinstance(v, (int, float)) and v > 0 else str(v)
            else:
                v = val.get("value") or val.get("number")
                if v is not None:
                    table[str(lvl)] = str(v)

        if not table:
            continue
        tables[ident] = table
        slug = _slugify(title)
        if slug != ident:
            tables[slug] = table

    return tables


def _norm_feature(doc: dict, path: str, scale_tables=None):
    name = doc.get("name", "").strip()
    if not name:
        return None
    system    = doc.get("system", {})
    desc_html = system.get("description", {}).get("value", "") if isinstance(system.get("description"), dict) else ""
    prereq    = system.get("prerequisites", {}) or {}
    feat_type = system.get("type", {}).get("value", "class") if isinstance(system.get("type"), dict) else "class"

    # Derive class from path: packs/_source/classes24/<class>/class-features/...
    # or races: packs/_source/races/<race>/<variant>-features/...
    parts      = path.replace("\\", "/").split("/")
    class_name = None
    if "classes24" in parts:
        idx        = parts.index("classes24")
        class_name = parts[idx + 1] if idx + 1 < len(parts) else None
    elif "races" in parts:
        feat_type = "race"

    # Resolve [[lookup @scale.class.identifier]] tokens before HTML stripping
    desc_html = _resolve_scale_tokens(desc_html, scale_tables or {})

    return {
        "name":        name,
        "index":       _slugify(name),
        "description": _strip_html(desc_html),
        "class":       class_name,
        "level_req":   prereq.get("level"),
        "type":        feat_type,
    }


# ─── Fetch 5e-bits datasets ───────────────────────────────────────────────────

def _load_bits_records(filename: str) -> list:
    raw = json.loads(_fetch(f"{RAW_5EBITS}/{filename}") or "null")
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("results", list(raw.values())[0] if raw else [])
    return []


def _build_5ebits(ruleset: str = DEFAULT_RULESET) -> tuple[dict, list[str]]:
    """Fetch all 5e-bits files for the given ruleset.

    Returns (categories, fallback_keys). fallback_keys lists categories
    that were sourced from 2014 because 2024 upstream doesn't have them
    yet. Recorded in _meta of the output for transparency.
    """
    global RAW_5EBITS  # _load_bits_records reads this
    fallback_keys: list[str] = []

    if ruleset == RULESET_2024:
        primary_files  = BITS_FILES_2024
        fallback_files = {k: v for k, v in BITS_FILES_2014.items()
                          if k in RULESET_2024_FALLBACK}
    else:
        primary_files  = BITS_FILES_2014
        fallback_files = {}

    NORM = {
        "spells":      _norm_spell,
        "equipment":   _norm_equipment,
        "magic_items": _norm_magic_item,
        "conditions":  _norm_condition,
        "monsters":    _norm_monster,
        # 2024-only categories — keep raw shape (no custom normaliser yet);
        # they pass through as-is so lookup.py can consume them.
        "weapon_mastery_properties": lambda r: r,
        "species":                   lambda r: r,
        "subspecies":                lambda r: r,
        "backgrounds":               lambda r: r,
        "feats":                     lambda r: r,
    }

    categories: dict = {}

    # Pass 1 — primary ruleset
    RAW_5EBITS = _bits_url(ruleset)
    for key, filename in primary_files.items():
        print(f"  5e-bits[{ruleset}]  {key} …", end="", flush=True)
        records = _load_bits_records(filename)
        if key in NORM:
            normed = [NORM[key](r) for r in records if isinstance(r, dict)]
            normed = [r for r in normed if r.get("name")]
        else:
            normed = [r for r in records if isinstance(r, dict) and r.get("name")]
        categories[key] = normed
        print(f" {len(normed)} records")

    # Pass 1.5 — for ruleset=2024, merge native foundry monsters from
    # packs/_source/actors24/. Foundry takes precedence on slug collision
    # over the (very small) 5e-bits 2024 monster set. After this pass,
    # actor coverage is typically ≥400, so the legacy 2014 augmentation
    # below ends up as a no-op and is omitted from _meta.fallback_2014.
    foundry_monster_count = 0
    if ruleset == RULESET_2024:
        print()
        print("── foundryvtt/dnd5e (2024 actors) ──────────────────────────────")
        foundry_monsters = _build_fvtt_monsters_2024()
        if foundry_monsters:
            existing = {r.get("index") or _slugify(r.get("name", "")):
                        r for r in categories.get("monsters", [])}
            # Foundry wins on slug collision; non-collisions from 5e-bits
            # 2024 (typically the 3 native monsters) are retained when
            # foundry doesn't ship them.
            merged: dict = {}
            for slug, rec in existing.items():
                merged[slug] = rec
            for rec in foundry_monsters:
                slug = rec.get("index") or _slugify(rec.get("name", ""))
                merged[slug] = rec  # overwrite/insert
            categories["monsters"] = list(merged.values())
            foundry_monster_count = len(foundry_monsters)
            print(f"  foundry[actors24]  merged → "
                  f"{len(categories['monsters'])} total monsters "
                  f"({foundry_monster_count} foundry-native)")

    # Pass 2 — for ruleset=2024 categories that are sparse upstream,
    # AUGMENT (not replace) with 2014 records. The 2024-native records
    # keep their primary status; 2014 records fill the gap and carry
    # _augmented_from_2014 = True so a downstream consumer can prefer
    # 2024-native if it cares about ruleset purity.
    #
    # When foundry actors24 returns ≥100 monsters, we skip the 2014
    # monster augmentation entirely (foundry already covers the SRD
    # comprehensively) and omit "monsters" from fallback_keys.
    if ruleset == RULESET_2024:
        RAW_5EBITS = _bits_url(RULESET_2014)
        for key in RULESET_2024_FALLBACK:
            # Skip 2014 monster augmentation when foundry already
            # provides comprehensive coverage.
            if key == "monsters" and foundry_monster_count >= 100:
                continue
            filename = BITS_FILES_2014.get(key)
            if not filename or key not in NORM:
                continue
            print(f"  5e-bits[2014 augment for {key}] …",
                  end="", flush=True)
            records = _load_bits_records(filename)
            normed_2014 = [NORM[key](r) for r in records if isinstance(r, dict)]
            normed_2014 = [r for r in normed_2014 if r.get("name")]
            # Tag each augmenter with provenance
            for r in normed_2014:
                r["_augmented_from_2014"] = True
            # Dedup by name — prefer 2024-native records (already in
            # categories[key]) over 2014 augmenters
            existing_names = {r["name"] for r in categories.get(key, [])}
            new_records = [r for r in normed_2014 if r["name"] not in existing_names]
            categories[key] = categories.get(key, []) + new_records
            fallback_keys.append(key)
            print(f" +{len(new_records)} from 2014 "
                  f"({len(existing_names)} native 2024 retained)")

    return categories, fallback_keys


# ─── Fetch FoundryVTT features ────────────────────────────────────────────────

def _build_fvtt():
    if yaml is None:
        print("  foundryvtt  skipped (PyYAML not installed)")
        return [], ""

    print("  foundryvtt  fetching repo tree …", end="", flush=True)
    data = _fetch_json(FVTT_TREE)
    if not data:
        print(" failed")
        return [], ""
    tree = data.get("tree", [])
    sha  = data.get("sha", "")

    # Partition tree into feature YAMLs and class-level YAMLs (scale tables)
    feature_paths  = []
    class_yml_paths = []
    for t in tree:
        p = t["path"]
        if not p.endswith(".yml") or os.path.basename(p).startswith("_"):
            continue
        if p.startswith("packs/_source/classes24/"):
            depth = p.count("/")
            if "/class-features/" in p:
                # e.g. packs/_source/classes24/rogue/class-features/SneakAttack.yml (5 slashes)
                feature_paths.append(p)
            elif depth == 4:
                # e.g. packs/_source/classes24/rogue/Rogue.yml — class document itself (4 slashes)
                class_yml_paths.append(p)
        elif p.startswith("packs/_source/races/") and re.search(r"/-?\w+-features/", p):
            feature_paths.append(p)

    print(f" {len(feature_paths)} feature files, {len(class_yml_paths)} class files")

    # Fetch class YAMLs and extract scale tables
    # scale_tables: {class_name: {identifier: {level_str: value_str}}}
    scale_tables = {}
    for path in class_yml_paths:
        raw = _fetch(f"{RAW_FVTT}/{path}")
        if not raw:
            continue
        try:
            doc = yaml.safe_load(raw)
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        parts = path.replace("\\", "/").split("/")
        if "classes24" not in parts:
            continue
        idx        = parts.index("classes24")
        class_name = parts[idx + 1] if idx + 1 < len(parts) else None
        if not class_name:
            continue
        tables = _parse_scale_tables(doc)
        if tables:
            scale_tables[class_name] = tables

    if scale_tables:
        resolved = sum(len(v) for v in scale_tables.values())
        print(f"  foundryvtt  {len(scale_tables)} classes, {resolved} scale tables loaded")

    # Fetch and normalise feature files
    features = []
    failed   = 0
    for i, path in enumerate(feature_paths, 1):
        raw = _fetch(f"{RAW_FVTT}/{path}")
        if raw is None:
            failed += 1
            continue
        try:
            doc = yaml.safe_load(raw)
        except Exception:
            failed += 1
            continue
        if not isinstance(doc, dict):
            continue
        feat = _norm_feature(doc, path, scale_tables)
        if feat and feat["description"]:
            features.append(feat)
        if i % 50 == 0:
            time.sleep(0.5)
            print(f"    … {i}/{len(feature_paths)}")

    print(f"  foundryvtt  {len(features)} features  ({failed} failed/empty)")
    return features, sha


# ─── Latest commit SHAs (for sync_srd.py) ────────────────────────────────────

def _latest_sha(url: str) -> str:
    data = _fetch_json(url)
    if data and isinstance(data, list) and data:
        return data[0].get("sha", "")
    return ""


# ─── Main ─────────────────────────────────────────────────────────────────────

def cmd_status(ruleset: str = DEFAULT_RULESET) -> None:
    out_file = _out_file(ruleset)
    if not os.path.exists(out_file):
        print(f"Dataset not built for ruleset {ruleset}. "
              f"Run build_srd.py --ruleset {ruleset} to create it.")
        return
    with open(out_file, encoding="utf-8") as f:
        data = json.load(f)
    meta    = data.get("_meta", {})
    counts  = meta.get("record_counts", {})
    sources = meta.get("sources", {})
    print(f"Dataset:    {out_file}")
    print(f"Ruleset:    {meta.get('ruleset', '(unspecified — assumed 2014)')}")
    print(f"Built at:   {meta.get('built_at','?')}")
    print()
    for cat, n in counts.items():
        print(f"  {cat:<28}  {n} records")
    print()
    for src, info in sources.items():
        print(f"  {src}:  {info.get('fetched_at','?')}  sha={info.get('sha','?')[:12]}…")
    fb = meta.get("fallback_2014", [])
    if fb:
        print()
        print(f"  Fallback 2014 categories: {', '.join(fb)}")
    license_attr = meta.get("license_attribution", [])
    if license_attr:
        print()
        for line in license_attr:
            print(f"  {line}")


def cmd_build(skip_fvtt: bool = False,
              ruleset: str = DEFAULT_RULESET) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out_file = _out_file(ruleset)

    print(f"── Building D&D 5e SRD dataset (ruleset: {ruleset}) ──────────")
    print()

    print("── 5e-bits/5e-database ─────────────────────────────────────────")
    bits_sha = _latest_sha(BITS_COMMITS)
    categories, fallback_keys = _build_5ebits(ruleset)

    # Spell handling — 2014 uses 5e-bits; 2024 uses foundryvtt/spells24
    if ruleset == RULESET_2024:
        print()
        print("── foundryvtt/dnd5e (2024 spells) ──────────────────────────────")
        if skip_fvtt:
            print("  skipped (--no-fvtt) — 2024 spells will be empty")
            categories["spells"] = []
        else:
            categories["spells"] = _build_fvtt_spells_2024()

    print()
    print("── foundryvtt/dnd5e (class features) ───────────────────────────")
    fvtt_sha = _latest_sha(FVTT_COMMITS)
    if skip_fvtt:
        print("  skipped (--no-fvtt)")
        features = []
    else:
        features, _ = _build_fvtt()
    categories["features"] = features

    counts = {k: len(v) for k, v in categories.items()}
    total  = sum(counts.values())

    license_attribution = [
        "Includes content from D&D 5e SRD 5.1 (CC-BY-4.0) — "
        "Wizards of the Coast.",
    ]
    if ruleset == RULESET_2024:
        license_attribution.append(
            "Includes content from D&D 5e SRD 5.2 (2024) (CC-BY-4.0) — "
            "Wizards of the Coast, via foundryvtt/dnd5e (MIT) and "
            "5e-bits/5e-database."
        )

    dataset = {
        "_meta": {
            "ruleset":       ruleset,
            "built_at":      now,
            "total_records": total,
            "record_counts": counts,
            "fallback_2014": fallback_keys,
            "license_attribution": license_attribution,
            "sources": {
                "5e-bits": {
                    "repo":       "5e-bits/5e-database",
                    "branch":     "main",
                    "subpath":    f"src/{ruleset}/en",
                    "sha":        bits_sha,
                    "fetched_at": now,
                },
                "foundryvtt": {
                    "repo":       "foundryvtt/dnd5e",
                    "branch":     "master",
                    "sha":        fvtt_sha,
                    "fetched_at": now,
                    "spells_2024_path": (FVTT_SPELLS_2024_PATH
                                         if ruleset == RULESET_2024 else None),
                },
            },
        },
        **categories,
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(dataset, f, separators=(",", ":"))

    size_kb = os.path.getsize(out_file) // 1024
    print()
    print(f"── Complete ────────────────────────────────────────────────────")
    print(f"  {total} records  →  {out_file}  ({size_kb} KB)")
    for cat, n in counts.items():
        print(f"    {cat:<28}  {n}")


def main() -> None:
    args = sys.argv[1:]
    # Parse --ruleset
    ruleset = DEFAULT_RULESET
    if "--ruleset" in args:
        i = args.index("--ruleset")
        if i + 1 < len(args):
            ruleset = args[i + 1]
            if ruleset not in (RULESET_2014, RULESET_2024):
                print(f"Unknown ruleset: {ruleset}. "
                      f"Valid: {RULESET_2014}, {RULESET_2024}.", file=sys.stderr)
                sys.exit(2)
    if "--status" in args:
        cmd_status(ruleset)
    else:
        cmd_build(skip_fvtt="--no-fvtt" in args, ruleset=ruleset)


if __name__ == "__main__":
    main()
