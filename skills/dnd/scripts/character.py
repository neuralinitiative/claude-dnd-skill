#!/usr/bin/env python3
"""
character.py — D&D 5e character stat calculator

Derives all secondary stats from raw inputs. Used by the DM (Claude)
to verify and generate character sheets without manual arithmetic.

Usage:
    python3 character.py calc --class fighter --level 1 \\
        STR=15 DEX=10 CON=15 INT=9 WIS=11 CHA=14 \\
        --proficient STR CON Athletics Intimidation Perception Survival

    python3 character.py calc --class cleric --level 1 --race dwarf \\
        --subrace hill-dwarf STR=15 DEX=10 CON=14 INT=9 WIS=13 CHA=11
        Scores given are the BASE array; racial bonuses are applied here and
        shown as base / race / final. An unknown race exits non-zero rather
        than applying nothing.

    python3 character.py calc --class fighter --level 1 --background soldier \\
        --background-asi STR=2,CON=1 STR=15 DEX=13 CON=14 INT=8 WIS=12 CHA=10
        2024 rules: the background raises its three listed abilities, either
        +2/+1 or +1/+1/+1 (`--background-asi +1/+1/+1`). SRD 5.2 backgrounds
        only; an unknown background or an ability it does not list exits
        non-zero.

    python3 character.py calc --class fighter ... --armor chain-mail --shield \\
        [--fighting-style defense]
        Armor Class from the SRD armor record. `--armor none` gives
        unarmored AC, with Unarmored Defense for a barbarian or monk.

    python3 character.py levelup --class fighter --from 1 --to 2 \\
        --hp-roll 6 --con-mod 2 [--con-mod-before 1] [--subclass champion] \\
        [--ruleset 2014|2024]
        Also lists the choices the new level owes: an Ability Score
        Improvement, the subclass, invocations, a pact boon, a fighting style,
        and HP owed retroactively when the CON modifier rose.

    python3 character.py hitdie --die d10 --con-mod 2 --hp 9 --max-hp 20 \\
        --remaining 3 [--count 2] [--ruleset 2014|2024 | -c <campaign>]
        Spend Hit Dice on a short rest: each die rolls + CON mod.

    python3 character.py rest long --level 5 --hd-remaining 1 --hp 9 \\
        --max-hp 38 [--exhaustion 1] [--ruleset 2014|2024 | -c <campaign>]
        What a long rest restores, gated on the character having at least 1 HP.

    python3 character.py xp --level 1 --gained 150
        Print XP total and whether level-up threshold is reached.
"""

import json
import os
import re
import secrets
import sys

# Windows / non-UTF-8 consoles: the output carries arrows and dashes, which a
# cp1252, GBK or ASCII stdout cannot encode. Same fix as the sibling scripts.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ─── Racial ability bonuses (SRD, 2014 rules) ────────────────────────────────
#
# THESE ARE APPLIED HERE, NOT REMEMBERED. The character-creation flow used to
# say "Apply racial bonuses" and then "run character.py calc" -- but calc had no
# `--race` parameter at all, so it never saw the race and could not check the
# step had happened. A Dwarf and an Elf built the same way came out with the
# same Constitution, and nothing in the toolchain could notice.
#
# Keyed by SRD index (lower-case, hyphenated), which is what `lookup.py race`
# reports, so the two agree by construction. Reconciled against the built SRD
# dataset by tests/test_racial_bonuses.py -- a drifted value fails there rather
# than silently producing a wrong sheet.
RACE_BONUSES = {
    "dwarf":      {"CON": 2},
    "elf":        {"DEX": 2},
    "halfling":   {"DEX": 2},
    "human":      {"STR": 1, "DEX": 1, "CON": 1, "INT": 1, "WIS": 1, "CHA": 1},
    "dragonborn": {"STR": 2, "CHA": 1},
    "gnome":      {"INT": 2},
    "half-elf":   {"CHA": 2},
    "half-orc":   {"STR": 2, "CON": 1},
    "tiefling":   {"INT": 1, "CHA": 2},
}

#: Subrace bonuses STACK on top of the parent race's. Half-Elf is the SRD's
#: one irregular case -- it grants +1 to two abilities of the player's choice
#: on top of its +2 CHA, which no table can express, so it is passed through
#: as a prompt rather than a number (see apply_racial_bonuses).
SUBRACE_BONUSES = {
    "hill-dwarf":         {"WIS": 1},
    "high-elf":           {"INT": 1},
    "lightfoot-halfling": {"CHA": 1},
    "rock-gnome":         {"CON": 1},
}

SUBRACE_PARENT = {
    "hill-dwarf": "dwarf", "high-elf": "elf",
    "lightfoot-halfling": "halfling", "rock-gnome": "gnome",
}

#: Races whose SRD entry grants a player CHOICE the tables above cannot hold.
#: Reported so the sheet writer is told to resolve it, instead of the choice
#: being silently dropped.
RACE_CHOICES = {
    "half-elf": "+1 to two abilities of your choice (SRD Half-Elf)",
}


def _race_key(name: str) -> str:
    """Normalise a race or subrace name to its SRD index."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


def apply_racial_bonuses(scores: dict, race: str = "", subrace: str = "") -> tuple:
    """Return (final_scores, applied, notes).

    `applied` maps stat -> total bonus added, so the caller can show base,
    racial and final rather than only the result. An unknown race RAISES: a
    typo that silently applied nothing is the whole defect this exists to
    stop, and a sheet that is quietly wrong is worse than a command that
    refuses.
    """
    out = dict(scores)
    applied: dict = {}
    notes: list = []
    if not race and not subrace:
        return out, applied, notes

    rkey = _race_key(race)
    skey = _race_key(subrace)

    if skey:
        if skey not in SUBRACE_BONUSES:
            raise ValueError(
                f"Unknown subrace {subrace!r}. Known: "
                f"{', '.join(sorted(SUBRACE_BONUSES))}")
        parent = SUBRACE_PARENT[skey]
        if rkey and rkey != parent:
            raise ValueError(
                f"Subrace {subrace!r} belongs to {parent!r}, not {race!r}")
        rkey = parent

    if rkey not in RACE_BONUSES:
        raise ValueError(
            f"Unknown race {race!r}. Known: {', '.join(sorted(RACE_BONUSES))}")

    for stat, bonus in RACE_BONUSES[rkey].items():
        applied[stat] = applied.get(stat, 0) + bonus
    if skey:
        for stat, bonus in SUBRACE_BONUSES[skey].items():
            applied[stat] = applied.get(stat, 0) + bonus

    for stat, bonus in applied.items():
        out[stat] = out.get(stat, 10) + bonus
    if rkey in RACE_CHOICES:
        notes.append(RACE_CHOICES[rkey])
    return out, applied, notes


# ─── Background ability increases (SRD 5.2, 2024 rules) ─────────────────────
#
# In the 2024 rules the ability increase comes from the background, not the
# species: +2 to one of the background's three abilities and +1 to another, or
# +1 to all three. Only the SRD 5.2 backgrounds are listed; reconciled against
# the 2024 upstream data by tests/test_character_rules.py.
BACKGROUND_ABILITIES = {
    "acolyte":  ("INT", "WIS", "CHA"),
    "criminal": ("DEX", "CON", "INT"),
    "sage":     ("CON", "INT", "WIS"),
    "soldier":  ("STR", "DEX", "CON"),
}


def parse_background_asi(spec: str, background: str) -> dict:
    """Validate a background increase and return {STAT: n}.

    Accepts "STR=2,CON=1" or "+1/+1/+1". Anything else -- an ability the
    background does not list, +2/+2, a total other than 3 -- raises, because
    a sheet with an increase the rules do not allow is the failure to stop.
    """
    key = _race_key(background)
    if key not in BACKGROUND_ABILITIES:
        raise ValueError(
            f"Unknown background {background!r}. SRD 5.2 backgrounds: "
            f"{', '.join(sorted(BACKGROUND_ABILITIES))}")
    allowed = BACKGROUND_ABILITIES[key]
    spec = (spec or "").replace(" ", "")
    if spec in ("+1/+1/+1", "1/1/1"):
        return {a: 1 for a in allowed}
    out: dict = {}
    for part in spec.split(","):
        if "=" not in part:
            raise ValueError(f"Bad --background-asi {spec!r}: use STR=2,DEX=1 or +1/+1/+1")
        stat, val = part.split("=", 1)
        stat = stat.upper()
        if stat not in allowed:
            raise ValueError(
                f"{background.title()} raises {', '.join(allowed)} only, not {stat}")
        if stat in out:
            raise ValueError(f"{stat} given twice in --background-asi")
        out[stat] = int(val.lstrip("+"))
    if sorted(out.values()) not in ([1, 2], [1, 1, 1]):
        raise ValueError(
            f"A background gives +2 and +1 to two of {', '.join(allowed)}, or +1 "
            f"to all three; got {spec!r}")
    return out


# ─── Armor Class ─────────────────────────────────────────────────────────────

_AC_PAT = re.compile(r"^AC\s*(\+?)(\d+)(\s*\+\s*DEX)?(?:\s*\(max\s*(\d+)\))?", re.I)


def _armor_records() -> dict:
    """SRD armor records keyed by index, from the bundled dataset."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from paths import data_dir
    path = data_dir() / "dnd5e_srd.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {r["index"]: r for r in data.get("equipment", []) if r.get("ac")}


def armor_class(scores: dict, cls: str, armor: str = "none", shield: bool = False,
                fighting_style: str = "", records: dict | None = None) -> tuple:
    """Return (ac, breakdown, notes) from an SRD armor index.

    Light armor adds DEX, medium caps it at +2, heavy adds none, a shield is
    +2 on top. With no armor a barbarian adds CON and a monk WIS (the monk
    only without a shield); the Defense fighting style is +1 while armor is
    worn. Raises on an armor index the dataset does not hold.
    """
    records = records if records is not None else _armor_records()
    dex = mod(scores.get("DEX", 10))
    notes: list = []
    parts: list = []
    worn = _race_key(armor or "none")
    if worn in ("", "none", "unarmored"):
        if cls == "barbarian":
            ac = 10 + dex + mod(scores.get("CON", 10))
            parts.append(f"Unarmored Defense 10 + DEX {fmt(dex)} + CON {fmt(mod(scores.get('CON', 10)))}")
        elif cls == "monk" and not shield:
            ac = 10 + dex + mod(scores.get("WIS", 10))
            parts.append(f"Unarmored Defense 10 + DEX {fmt(dex)} + WIS {fmt(mod(scores.get('WIS', 10)))}")
        else:
            ac = 10 + dex
            parts.append(f"unarmored 10 + DEX {fmt(dex)}")
        wearing = False
    else:
        rec = records.get(worn)
        if not rec or worn == "shield":
            known = ", ".join(sorted(k for k in records if k != "shield"))
            raise ValueError(f"Unknown armor {armor!r}. SRD armor: {known}")
        m = _AC_PAT.match(rec["ac"])
        base, adds_dex, cap = int(m.group(2)), bool(m.group(3)), m.group(4)
        bonus = 0
        if adds_dex:
            bonus = min(dex, int(cap)) if cap else dex
        ac = base + bonus
        parts.append(f"{rec['name']} {base}" + (f" + DEX {fmt(bonus)}" if adds_dex else ""))
        if rec.get("str_minimum") and scores.get("STR", 10) < rec["str_minimum"]:
            notes.append(f"STR {scores.get('STR', 10)} is below {rec['name']}'s "
                         f"{rec['str_minimum']}: speed -10 ft")
        if rec.get("stealth_disadv"):
            notes.append(f"{rec['name']}: Disadvantage on Stealth")
        wearing = True
    if shield:
        ac += 2
        parts.append("Shield +2")
    if _race_key(fighting_style) == "defense":
        if wearing:
            ac += 1
            parts.append("Defense +1")
        else:
            notes.append("Defense applies only while wearing armor")
    return ac, " + ".join(parts), notes


# ─── Proficiency bonus by level ──────────────────────────────────────────────
PROF_BONUS = {1:2, 2:2, 3:2, 4:2, 5:3, 6:3, 7:3, 8:3,
              9:4, 10:4, 11:4, 12:4, 13:5, 14:5, 15:5, 16:5,
              17:6, 18:6, 19:6, 20:6}

# ─── XP thresholds (total XP needed to reach level) ─────────────────────────
XP_THRESHOLDS = {
    1: 0, 2: 300, 3: 900, 4: 2700, 5: 6500, 6: 14000,
    7: 23000, 8: 34000, 9: 48000, 10: 64000, 11: 85000,
    12: 100000, 13: 120000, 14: 140000, 15: 165000,
    16: 195000, 17: 225000, 18: 265000, 19: 305000, 20: 355000,
}

# ─── Hit dice by class ───────────────────────────────────────────────────────
HIT_DICE = {
    "barbarian": 12, "fighter": 10, "paladin": 10, "ranger": 10,
    "bard": 8, "cleric": 8, "druid": 8, "monk": 8, "rogue": 8, "warlock": 8,
    "sorcerer": 6, "wizard": 6,
}

# ─── Saving throw proficiencies by class ─────────────────────────────────────
SAVE_PROFS = {
    "fighter":   ["STR", "CON"],
    "barbarian": ["STR", "CON"],
    "ranger":    ["STR", "DEX"],
    "paladin":   ["WIS", "CHA"],
    "rogue":     ["DEX", "INT"],
    "bard":      ["DEX", "CHA"],
    "cleric":    ["WIS", "CHA"],
    "druid":     ["INT", "WIS"],
    "monk":      ["STR", "DEX"],
    "warlock":   ["WIS", "CHA"],
    "sorcerer":  ["CON", "CHA"],
    "wizard":    ["INT", "WIS"],
}

# ─── All skills with governing ability ───────────────────────────────────────
SKILLS = {
    "Acrobatics": "DEX", "Animal Handling": "WIS", "Arcana": "INT",
    "Athletics": "STR", "Deception": "CHA", "History": "INT",
    "Insight": "WIS", "Intimidation": "CHA", "Investigation": "INT",
    "Medicine": "WIS", "Nature": "INT", "Perception": "WIS",
    "Performance": "CHA", "Persuasion": "CHA", "Religion": "INT",
    "Sleight of Hand": "DEX", "Stealth": "DEX", "Survival": "WIS",
}

STATS = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]


def mod(score: int) -> int:
    return (score - 10) // 2


def fmt(n: int) -> str:
    return f"+{n}" if n >= 0 else str(n)


def parse_scores(args: list[str]) -> dict[str, int]:
    scores = {}
    for i, a in enumerate(args):
        if i and args[i - 1] == "--background-asi":
            continue   # "STR=2,CON=1" is the increase, not a score
        if "=" in a:
            k, v = a.split("=", 1)
            if k.upper() in STATS:
                scores[k.upper()] = int(v)
    return scores


def parse_proficient(args: list[str]) -> list[str]:
    """Everything after --proficient until next -- flag."""
    if "--proficient" not in args:
        return []
    idx = args.index("--proficient")
    profs = []
    for a in args[idx + 1:]:
        if a.startswith("--"):
            break
        profs.append(a)
    return profs


def do_calc(args: list[str]):
    cls = args[args.index("--class") + 1].lower() if "--class" in args else "fighter"
    lvl = int(args[args.index("--level") + 1]) if "--level" in args else 1
    base_scores = parse_scores(args)
    proficient = parse_proficient(args)  # list of stat names + skill names
    race = args[args.index("--race") + 1] if "--race" in args else ""
    subrace = args[args.index("--subrace") + 1] if "--subrace" in args else ""
    background = args[args.index("--background") + 1] if "--background" in args else ""
    bg_spec = args[args.index("--background-asi") + 1] if "--background-asi" in args else ""
    armor = args[args.index("--armor") + 1] if "--armor" in args else None
    style = args[args.index("--fighting-style") + 1] if "--fighting-style" in args else ""

    # The scores passed in are the BASE array, before racial bonuses. Applying
    # them here is what makes the step verifiable: an unknown race raises
    # rather than applying nothing.
    try:
        if background and (race or subrace):
            raise ValueError(
                "--race (2014) and --background (2024) both grant ability "
                "increases; a character takes one or the other")
        scores, racial, race_notes = apply_racial_bonuses(base_scores, race, subrace)
        bg_bonus: dict = {}
        if background:
            key = _race_key(background)
            if key not in BACKGROUND_ABILITIES:
                parse_background_asi("", background)   # raises: unknown background
            if bg_spec:
                bg_bonus = parse_background_asi(bg_spec, background)
            else:
                race_notes.append(
                    f"{background.title()} background increase: +2/+1 or +1/+1/+1 "
                    f"among {', '.join(BACKGROUND_ABILITIES[key])} "
                    f"(--background-asi)")
            for stat, n in bg_bonus.items():
                scores[stat] = scores.get(stat, 10) + n
                if scores[stat] > 20:
                    raise ValueError(f"{stat} would be {scores[stat]}; an increase "
                                     f"cannot raise a score above 20")
        ac_info = None
        if armor is not None or "--shield" in args:
            ac_info = armor_class(scores, cls, armor or "none", "--shield" in args, style)
    except ValueError as exc:
        print(f"character.py: {exc}", file=sys.stderr)
        sys.exit(2)

    prof = PROF_BONUS.get(lvl, 2)
    hd = HIT_DICE.get(cls, 8)
    con_mod = mod(scores.get("CON", 10))
    hp = hd + con_mod  # level 1 max
    save_profs = SAVE_PROFS.get(cls, [])

    print(f"\n{'='*50}")
    print(f"  {cls.title()} Level {lvl}  |  Proficiency Bonus: +{prof}")
    print(f"{'='*50}")

    if bg_bonus or (background and not racial):
        label = background.strip().title()
        print(f"\n  Ability Scores  (base + {label} background increase):")
        print(f"  {'Stat':<6} {'Base':>6} {'Bkgd':>6} {'Score':>6} {'Mod':>5}")
        print(f"  {'-'*33}")
        for s in STATS:
            base = base_scores.get(s, 10)
            bonus = bg_bonus.get(s, 0)
            score = scores.get(s, 10)
            print(f"  {s:<6} {base:>6} {fmt(bonus) if bonus else '—':>6} "
                  f"{score:>6} {fmt(mod(score)):>5}")
        for note in race_notes:
            print(f"\n  ! Still to assign: {note}")
    elif racial:
        label = (subrace or race).strip()
        print(f"\n  Ability Scores  (base + {label} racial bonus):")
        print(f"  {'Stat':<6} {'Base':>6} {'Race':>6} {'Score':>6} {'Mod':>5}")
        print(f"  {'-'*33}")
        for s in STATS:
            base = base_scores.get(s, 10)
            bonus = racial.get(s, 0)
            score = scores.get(s, 10)
            print(f"  {s:<6} {base:>6} {fmt(bonus) if bonus else '—':>6} "
                  f"{score:>6} {fmt(mod(score)):>5}")
        for note in race_notes:
            print(f"\n  ! Still to assign: {note}")
    else:
        print(f"\n  Ability Scores:")
        print(f"  {'Stat':<6} {'Score':>6} {'Mod':>5}")
        print(f"  {'-'*20}")
        for s in STATS:
            score = scores.get(s, 10)
            print(f"  {s:<6} {score:>6} {fmt(mod(score)):>5}")

    print(f"\n  Combat Stats:")
    print(f"  HP (level 1): {hp}  ({hd} + CON {fmt(con_mod)})")
    print(f"  Hit Dice: {lvl}d{hd}")
    print(f"  Initiative: {fmt(mod(scores.get('DEX', 10)))}")
    if ac_info:
        ac, breakdown, ac_notes = ac_info
        print(f"  Armor Class: {ac}  ({breakdown})")
        for note in ac_notes:
            print(f"    ! {note}")

    print(f"\n  Saving Throws (* = proficient):")
    for s in STATS:
        score = scores.get(s, 10)
        is_prof = s in save_profs or s in proficient
        bonus = mod(score) + (prof if is_prof else 0)
        marker = "*" if is_prof else " "
        print(f"  {marker} {s:<4} {fmt(bonus)}")

    print(f"\n  Skills (* = proficient):")
    for skill, ability in sorted(SKILLS.items()):
        score = scores.get(ability, 10)
        is_prof = skill in proficient or skill.lower() in [p.lower() for p in proficient]
        bonus = mod(score) + (prof if is_prof else 0)
        marker = "*" if is_prof else " "
        print(f"  {marker} {skill:<22} ({ability})  {fmt(bonus)}")

    print(f"\n  XP to next level: {XP_THRESHOLDS.get(lvl+1, 'MAX')} total")
    print()


# ─── Choices a level-up owes ─────────────────────────────────────────────────
#
# A level-up was reported as HP and proficiency only, so the choices the new
# level grants were left to memory: a fighter reaching 6 with no ASI taken, a
# warlock at 2 with no invocations, a paladin at 2 with no fighting style.

ASI_LEVELS = {
    "fighter": (4, 6, 8, 12, 14, 16, 19),
    "rogue":   (4, 8, 10, 12, 16, 19),
}
ASI_LEVELS_DEFAULT = (4, 8, 12, 16, 19)

#: Level the subclass is chosen, 2014 (SRD 5.1). Every class chooses at 3 in 2024.
SUBCLASS_LEVEL_2014 = {
    "barbarian": 3, "bard": 3, "cleric": 1, "druid": 2, "fighter": 3, "monk": 3,
    "paladin": 3, "ranger": 3, "rogue": 3, "sorcerer": 1, "warlock": 1, "wizard": 2,
}
SUBCLASS_NAME = {
    "barbarian": "Primal Path", "bard": "Bard College", "cleric": "Divine Domain",
    "druid": "Druid Circle", "fighter": "Martial Archetype", "monk": "Monastic Tradition",
    "paladin": "Sacred Oath", "ranger": "Ranger Archetype", "rogue": "Roguish Archetype",
    "sorcerer": "Sorcerous Origin", "warlock": "Otherworldly Patron",
    "wizard": "Arcane Tradition",
}

#: Eldritch Invocations known, from the class tables. The 2014 column follows
#: the SRD 5.1 Warlock table; the 5e-database Levels file has it one higher at
#: levels 4 and 6, which would owe a warlock an invocation the rules do not
#: grant. tests/test_character_rules.py pins both columns.
INVOCATIONS_KNOWN = {
    "2014": {1: 0, 2: 2, 3: 2, 4: 2, 5: 3, 6: 3, 7: 4, 8: 4, 9: 5, 10: 5, 11: 5,
             12: 6, 13: 6, 14: 6, 15: 7, 16: 7, 17: 7, 18: 8, 19: 8, 20: 8},
    "2024": {1: 1, 2: 3, 3: 3, 4: 3, 5: 5, 6: 5, 7: 6, 8: 6, 9: 7, 10: 7, 11: 7,
             12: 8, 13: 8, 14: 8, 15: 9, 16: 9, 17: 9, 18: 10, 19: 10, 20: 10},
}

FIGHTING_STYLE_LEVEL = {"fighter": 1, "paladin": 2, "ranger": 2}
#: Champion's Additional Fighting Style: level 10 in 2014, 7 in 2024.
CHAMPION_STYLE_LEVEL = {"2014": 10, "2024": 7}


def _class_options(kind: str) -> list:
    """Option names of one kind from the bundled dataset, or [] without it."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from paths import data_dir
        data = json.loads((data_dir() / "dnd5e_srd.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in data.get("class_options", []) if r.get("kind") == kind]


def owed_choices(cls: str, from_lvl: int, to_lvl: int, ruleset: str = "2014",
                 subclass: str = "") -> list:
    """The choices reaching `to_lvl` grants, as human-readable lines."""
    cls = cls.lower()
    out: list = []
    for lvl in range(from_lvl + 1, to_lvl + 1):
        if lvl in ASI_LEVELS.get(cls, ASI_LEVELS_DEFAULT):
            if ruleset == "2024" and lvl == 19:
                out.append(f"Level {lvl}: Epic Boon feat")
            elif ruleset == "2024":
                out.append(f"Level {lvl}: Ability Score Improvement feat (or another feat)")
            else:
                out.append(f"Level {lvl}: Ability Score Improvement — +2 to one score or "
                           f"+1 to two (or a feat, if the table uses feats)")
        sub_lvl = 3 if ruleset == "2024" else SUBCLASS_LEVEL_2014.get(cls)
        if lvl == sub_lvl:
            out.append(f"Level {lvl}: choose a subclass ({SUBCLASS_NAME.get(cls, 'subclass')})")
        if lvl == FIGHTING_STYLE_LEVEL.get(cls):
            out.append(f"Level {lvl}: choose a Fighting Style")
        if cls == "fighter" and _race_key(subclass) == "champion" \
                and lvl == CHAMPION_STYLE_LEVEL.get(ruleset, 10):
            out.append(f"Level {lvl}: Champion — choose an Additional Fighting Style")
        if cls == "warlock":
            table = INVOCATIONS_KNOWN.get(ruleset, INVOCATIONS_KNOWN["2014"])
            gained = table.get(lvl, 0) - table.get(lvl - 1, 0)
            if gained > 0:
                out.append(f"Level {lvl}: learn {gained} Eldritch Invocation"
                           f"{'s' if gained > 1 else ''} (now {table[lvl]} known)")
            if ruleset != "2024" and lvl == 3:
                out.append("Level 3: choose a Pact Boon (Chain, Blade or Tome)")
    return out


def retro_hp(con_before: int, con_after: int, levels: int) -> int:
    """HP owed for earlier levels when the CON modifier changes: one per level."""
    return (con_after - con_before) * levels


def _ruleset_from(args: list) -> str:
    if "--ruleset" in args:
        rs = args[args.index("--ruleset") + 1]
        if rs not in ("2014", "2024"):
            print(f"character.py: --ruleset must be 2014 or 2024, not {rs!r}", file=sys.stderr)
            sys.exit(2)
        return rs
    for flag in ("-c", "--campaign"):
        if flag in args:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from paths import campaign_ruleset
            return campaign_ruleset(args[args.index(flag) + 1])
    return "2014"


def do_levelup(args: list[str]):
    cls = args[args.index("--class") + 1].lower() if "--class" in args else "fighter"
    from_lvl = int(args[args.index("--from") + 1]) if "--from" in args else 1
    to_lvl = from_lvl + 1
    hp_roll = int(args[args.index("--hp-roll") + 1]) if "--hp-roll" in args else None
    con_mod_val = int(args[args.index("--con-mod") + 1]) if "--con-mod" in args else 0
    con_before = (int(args[args.index("--con-mod-before") + 1])
                  if "--con-mod-before" in args else None)
    subclass = args[args.index("--subclass") + 1] if "--subclass" in args else ""
    ruleset = _ruleset_from(args)

    hd = HIT_DICE.get(cls, 8)
    prof_old = PROF_BONUS.get(from_lvl, 2)
    prof_new = PROF_BONUS.get(to_lvl, 2)

    print(f"\n  Level Up: {cls.title()} {from_lvl} → {to_lvl}")
    print(f"  Proficiency bonus: +{prof_old} → +{prof_new}")

    if hp_roll is not None:
        hp_gained = hp_roll + con_mod_val
        print(f"  HP gained: d{hd}({hp_roll}) + CON({fmt(con_mod_val)}) = {hp_gained}")
    else:
        avg = (hd // 2 + 1) + con_mod_val
        print(f"  HP gained (avg): {avg}  ({hd//2+1} + CON {fmt(con_mod_val)})")

    if con_before is not None and con_before != con_mod_val:
        owed = retro_hp(con_before, con_mod_val, from_lvl)
        print(f"  CON modifier {fmt(con_before)} → {fmt(con_mod_val)}: "
              f"{fmt(owed)} max HP for the {from_lvl} earlier level"
              f"{'s' if from_lvl != 1 else ''} as well")

    print(f"  XP threshold for level {to_lvl}: {XP_THRESHOLDS.get(to_lvl, 'MAX')}")

    owed_list = owed_choices(cls, from_lvl, to_lvl, ruleset, subclass)
    if owed_list:
        print(f"\n  Choices owed ({ruleset} rules):")
        for line in owed_list:
            print(f"    - {line}")
        if cls == "warlock" and any("Invocation" in x for x in owed_list):
            names = [r["name"] for r in _class_options("invocation")
                     if (r.get("level") or 0) <= to_lvl]
            if names and ruleset == "2014":
                print(f"      SRD invocations available: {', '.join(sorted(names))}")
            print("      (lookup.py invocation <name> for each one's text)")
        if any("Fighting Style" in x for x in owed_list) and ruleset == "2014":
            styles = sorted({r["name"] for r in _class_options("fighting_style")
                             if r.get("class", "").lower() == cls})
            if styles:
                print(f"      SRD fighting styles for a {cls}: {', '.join(styles)}")
        if any("Fighting Style" in x for x in owed_list) and ruleset == "2024":
            print("      (2024: a Fighting Style feat — see the SRD 5.2 feat list)")
        if any("Pact Boon" in x for x in owed_list):
            print("      (lookup.py pact-boon <name> for Chain, Blade and Tome)")
    print()


# ─── Hit Dice and long rests ─────────────────────────────────────────────────

def _int_arg(args: list, flag: str, default=None):
    if flag in args:
        return int(args[args.index(flag) + 1])
    if default is None:
        print(f"character.py: {flag} is required", file=sys.stderr)
        sys.exit(2)
    return default


def spend_hit_dice(die: int, con_mod: int, hp: int, max_hp: int, remaining: int,
                   count: int = 1, ruleset: str = "2014", rolls: list | None = None) -> dict:
    """Spend `count` Hit Dice. Raises ValueError when the rules refuse it.

    Each die heals its roll + CON modifier, never below 0 (2014) or 1 (2024),
    and the total stops at max HP. A character at 0 HP cannot spend them,
    and nobody can spend dice they do not have.
    """
    if hp <= 0:
        raise ValueError("at 0 HP a character cannot spend Hit Dice — they need "
                         "at least 1 HP (stabilize and heal first)")
    if remaining <= 0:
        raise ValueError("no Hit Dice left to spend")
    if count > remaining:
        raise ValueError(f"only {remaining} Hit Dice left, cannot spend {count}")
    floor = 1 if ruleset == "2024" else 0
    rolls = list(rolls) if rolls is not None else [secrets.randbelow(die) + 1 for _ in range(count)]
    per_die = [max(floor, r + con_mod) for r in rolls]
    new_hp = min(max_hp, hp + sum(per_die))
    return {"rolls": rolls, "healed": per_die, "hp": new_hp,
            "remaining": remaining - count, "wasted": hp + sum(per_die) - new_hp}


def long_rest(level: int, hd_remaining: int, hp: int, max_hp: int,
              ruleset: str = "2014", exhaustion: int = 0) -> dict:
    """What a long rest restores. A character at 0 HP gains nothing from it.

    Hit Dice: half the character's total, rounded down, minimum 1 (2014), or
    all of them (2024). Exhaustion drops one level.
    """
    if hp <= 0:
        return {"benefit": False,
                "reason": "at 0 HP — a long rest gives no benefit until the "
                          "character has at least 1 HP"}
    regained = level if ruleset == "2024" else max(1, level // 2)
    new_hd = min(level, hd_remaining + regained)
    return {"benefit": True, "hp": max_hp, "hd_remaining": new_hd,
            "hd_regained": new_hd - hd_remaining, "exhaustion": max(0, exhaustion - 1)}


def do_hitdie(args: list[str]):
    die_s = args[args.index("--die") + 1] if "--die" in args else ""
    m = re.match(r"^d?(\d+)$", die_s.strip().lower())
    if not m:
        print("character.py: --die d6|d8|d10|d12 is required", file=sys.stderr)
        sys.exit(2)
    ruleset = _ruleset_from(args)
    try:
        r = spend_hit_dice(int(m.group(1)), _int_arg(args, "--con-mod", 0),
                           _int_arg(args, "--hp"), _int_arg(args, "--max-hp"),
                           _int_arg(args, "--remaining"), _int_arg(args, "--count", 1),
                           ruleset)
    except ValueError as exc:
        print(f"character.py: {exc}", file=sys.stderr)
        sys.exit(2)
    con = _int_arg(args, "--con-mod", 0)
    for roll, healed in zip(r["rolls"], r["healed"]):
        print(f"  Hit Die: d{m.group(1)}({roll}) + CON {fmt(con)} = {healed}")
    print(f"  HP: {_int_arg(args, '--hp')} → {r['hp']} / {_int_arg(args, '--max-hp')}"
          + (f"  ({r['wasted']} over max, lost)" if r["wasted"] else ""))
    print(f"  Hit Dice left: {r['remaining']}")


def do_rest(args: list[str]):
    if not args or args[0] != "long":
        print("character.py: usage: rest long --level N --hd-remaining N --hp N --max-hp N",
              file=sys.stderr)
        sys.exit(2)
    ruleset = _ruleset_from(args)
    r = long_rest(_int_arg(args, "--level"), _int_arg(args, "--hd-remaining"),
                  _int_arg(args, "--hp"), _int_arg(args, "--max-hp"), ruleset,
                  _int_arg(args, "--exhaustion", 0))
    if not r["benefit"]:
        print(f"  No long-rest benefit: {r['reason']}")
        return
    print(f"  Long rest ({ruleset} rules):")
    print(f"    HP: → {r['hp']} (full)")
    print(f"    Hit Dice: +{r['hd_regained']} → {r['hd_remaining']} / {_int_arg(args, '--level')}")
    if _int_arg(args, "--exhaustion", 0):
        print(f"    Exhaustion: {_int_arg(args, '--exhaustion', 0)} → {r['exhaustion']}")
    print("    Spell slots and long-rest features: restored. Concentration: ends.")


def do_xp(args: list[str]):
    lvl = int(args[args.index("--level") + 1]) if "--level" in args else 1
    gained = int(args[args.index("--gained") + 1]) if "--gained" in args else 0
    current = XP_THRESHOLDS.get(lvl, 0)
    next_lvl = XP_THRESHOLDS.get(lvl + 1)
    new_total = current + gained

    print(f"\n  XP: {current} + {gained} gained = {new_total}")
    if next_lvl:
        if new_total >= next_lvl:
            print(f"  *** Level up available! Reached {next_lvl} (Level {lvl+1} threshold) ***")
        else:
            print(f"  Next level ({lvl+1}) at: {next_lvl}  ({next_lvl - new_total} XP remaining)")
    else:
        print("  Maximum level reached.")
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "calc":
        do_calc(args)
    elif cmd == "levelup":
        do_levelup(args)
    elif cmd == "xp":
        do_xp(args)
    elif cmd == "hitdie":
        do_hitdie(args)
    elif cmd == "rest":
        do_rest(args)
    else:
        print(f"Unknown command: {cmd}. Use: calc | levelup | hitdie | rest | xp")
        sys.exit(1)
