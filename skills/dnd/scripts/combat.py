#!/usr/bin/env python3

"""
combat.py — D&D 5e combat tracker

Usage:
    python3 combat.py init <combatants_json>
        Rolls initiative for all combatants and prints turn order.
        combatants_json: JSON array of {"name": str, "dex_mod": int, "hp": int, "ac": int, "type": "pc"|"npc"}

    python3 combat.py tracker <state_json>
        Prints the current combat tracker table from a JSON state blob.

    python3 combat.py attack --atk <bonus> --ac <target_ac> --dmg <notation> [--crit]
        Resolves a single attack roll and damage.

Input / Output is JSON-friendly so the DM (Claude) can pipe state between turns.

Example:
    python3 combat.py init '[{"name":"Flerb","dex_mod":0,"hp":12,"ac":16,"type":"pc"},
                              {"name":"Goblin","dex_mod":1,"hp":7,"ac":15,"type":"npc"}]'
"""

from __future__ import annotations  # PEP 604 annotations on Python 3.9

# Windows UTF-8 fix: GBK stdout crashes on unicode glyphs (► etc.) when piped.
import sys
if hasattr(sys.stdout, "reconfigure"):
    try: sys.stdout.reconfigure(encoding="utf-8")
    # Swallowed on purpose: a stdout we cannot reconfigure is still a usable
    # stdout, and refusing to start a combat over it would be the worse trade.
    except Exception: pass

import json
import random
import sys
import re


def roll(n, sides):
    return [random.randint(1, sides) for _ in range(n)]


def dice(notation: str) -> tuple[int, list[int]]:
    """Parse NdS+M notation, return (total, individual_rolls)."""
    m = re.match(r'^(\d*)d(\d+)([+-]\d+)?$', notation.strip().lower())
    if not m:
        raise ValueError(f"Bad dice notation: {notation}")
    n = int(m.group(1)) if m.group(1) else 1
    s = int(m.group(2))
    mod = int(m.group(3)) if m.group(3) else 0
    rolls = roll(n, s)
    return sum(rolls) + mod, rolls


def initiative_order(combatants: list[dict]) -> list[dict]:
    """Roll d20+dex_mod for each combatant, sort descending."""
    for c in combatants:
        raw = random.randint(1, 20)
        c["initiative_roll"] = raw
        c["initiative"] = raw + c.get("dex_mod", 0)
        c["conditions"] = []
        c["temp_hp"] = 0
    return sorted(combatants, key=lambda x: (x["initiative"], x.get("dex_mod", 0)), reverse=True)


def print_tracker(combatants: list[dict], round_num: int = 1):
    print(f"\n{'='*68}")
    print(f"  COMBAT — Round {round_num}")
    print(f"{'='*68}")
    print(f"  {'#':<3} {'Name':<18} {'Init':>5} {'HP':>8} {'AC':>4}  Conditions")
    print(f"  {'-'*62}")
    for i, c in enumerate(combatants, 1):
        hp_str = f"{c['hp']}/{c.get('max_hp', c['hp'])}"
        cond = ", ".join(c.get("conditions", [])) or "—"
        marker = "► " if i == 1 else "  "
        print(f"  {marker}{i:<2} {c['name']:<18} {c['initiative']:>5} {hp_str:>8} {c['ac']:>4}  {cond}")
    print(f"{'='*68}\n")


def resolve_attack(atk_bonus: int, target_ac: int, dmg_notation: str, is_crit: bool = False) -> dict:
    raw = random.randint(1, 20)
    total_atk = raw + atk_bonus
    hit = raw == 20 or (raw != 1 and total_atk >= target_ac)
    crit = raw == 20

    result = {
        "d20": raw,
        "attack_bonus": atk_bonus,
        "total": total_atk,
        "target_ac": target_ac,
        "hit": hit,
        "crit": crit,
        "fumble": raw == 1,
    }

    if hit:
        dmg, rolls = dice(dmg_notation)
        if crit:
            # Double the dice rolls on crit
            extra, extra_rolls = dice(dmg_notation.split("+")[0].split("-")[0])
            dmg += extra
            rolls += extra_rolls
        result["damage"] = dmg
        result["damage_rolls"] = rolls
        result["damage_notation"] = dmg_notation

    return result


def format_attack(r: dict) -> str:
    lines = []
    flag = ""
    if r["crit"]:
        flag = " *** CRITICAL HIT! ***"
    elif r["fumble"]:
        flag = " *** FUMBLE — automatic miss ***"

    atk_str = f"d20({r['d20']}) + {r['attack_bonus']} = {r['total']} vs AC {r['target_ac']}"
    outcome = "HIT" if r["hit"] else "MISS"
    lines.append(f"Attack: {atk_str} — {outcome}{flag}")

    if r.get("damage") is not None:
        note = " (crit: doubled dice)" if r["crit"] else ""
        lines.append(f"Damage: {r['damage_rolls']} + mod = {r['damage']} {r['damage_notation'].split('+')[0].split('-')[0][1:]}dmg{note}")

    if r.get("mastery_text"):
        lines.append(f"Mastery: {r['mastery_text']}")

    return "\n".join(lines)


# ── Weapon mastery (D&D 2024) ──────────────────────────────────────────────
# Each property emits a narrative + mechanical-effect text outcome. Claude
# weaves the text into narration; tracker.py / SKILL.md handle any persistent
# state (e.g. "next attack has advantage" → tracker effect-start). The script
# does not auto-apply state — it keeps the DM in the loop.
#
# Properties match the 2024 PHB. Some require a save (Topple) — the script
# rolls the save when --topple-dc is supplied; otherwise emits the call-out
# text and lets the DM resolve.
#
# Source: D&D 2024 SRD 5.2 Weapon-Mastery-Properties (CC-BY-4.0).

MASTERY_PROPERTIES = {
    "cleave":  ("On hit, you may make a second attack against a different "
                "creature within 5 ft of the first using the same roll. "
                "Bonus damage on second hit only includes Strength/Dexterity "
                "modifier from the first."),
    "graze":   ("On miss, the target still takes damage equal to the ability "
                "modifier you used for the attack."),
    "nick":    ("Light-weapon extra attack costs no Bonus Action this turn "
                "(the second attack is part of the same Attack action)."),
    "push":    ("On hit, the target (Large or smaller) is pushed up to 10 ft "
                "directly away from you."),
    "sap":     ("On hit, the target has Disadvantage on its next attack roll "
                "before the start of your next turn."),
    "slow":    ("On hit, the target's Speed is reduced by 10 ft until the "
                "start of your next turn."),
    "topple":  ("On hit, target makes a CON save (DC 8 + prof + STR mod) or "
                "is knocked Prone."),
    "vex":     ("On hit, you have Advantage on your next attack roll against "
                "this target before the end of your next turn."),
}


def apply_mastery(property_name: str, hit: bool, ability_mod: int = 0,
                  save_dc: int | None = None) -> dict:
    """Return a text + structured outcome for a weapon mastery property.

    Args:
        property_name: lowercased property key (cleave, graze, nick, etc.)
        hit: whether the attack hit
        ability_mod: STR or DEX modifier used for the attack — needed for
            graze damage and topple save DC if save_dc is omitted
        save_dc: optional explicit save DC (Topple defaults to 8 + 2 + mod
            without proficiency context; pass an explicit DC to override).

    Returns a dict with:
      - property: canonical property name
      - text: human-readable effect string (DM weaves into narration)
      - applies: bool — whether the property actually triggers this attack
        (e.g. graze applies on miss, others on hit)
      - save_roll, save_total, save_passed: only present for Topple
    """
    prop = property_name.lower()
    if prop not in MASTERY_PROPERTIES:
        return {"property": prop, "text": f"Unknown mastery: {property_name}",
                "applies": False}

    description = MASTERY_PROPERTIES[prop]
    out: dict = {"property": prop, "description": description, "applies": False}

    if prop == "graze":
        # Triggers only on miss; deals damage = ability_mod
        if hit:
            out["text"] = "(graze does not apply on hits — full damage rolled)"
            return out
        dmg = max(0, ability_mod)
        out["applies"] = True
        out["text"] = f"Graze — target still takes {dmg} damage (ability mod)"
        out["graze_damage"] = dmg
        return out

    if prop == "nick":
        # Always applies on a Light-weapon attack — the DM must arrange the
        # extra attack via Attack action; nick frees the bonus-action slot.
        out["applies"] = True
        out["text"] = ("Nick — the Light-weapon extra attack uses no Bonus "
                       "Action this turn")
        return out

    if not hit:
        # Most other masteries trigger only on hit
        out["text"] = f"({prop} requires a hit — attack missed; no effect)"
        return out

    out["applies"] = True

    if prop == "topple":
        # Roll save if DC provided
        dc = save_dc or (8 + 2 + ability_mod)  # 8 + (assumed prof 2) + mod
        save_roll = random.randint(1, 20)
        save_total = save_roll + ability_mod  # CON save uses target's CON;
        # caller should pass save_dc directly and add the target's CON mod
        # in their own roll instead. This is a fallback when no DC supplied.
        passed = save_total >= dc
        out["save_dc"]     = dc
        out["save_roll"]   = save_roll
        out["save_total"]  = save_total
        out["save_passed"] = passed
        out["text"]        = (f"Topple — target rolls CON save vs DC {dc}: "
                              f"d20({save_roll}) + mod = {save_total} "
                              f"→ {'saved' if passed else 'PRONE'}")
        return out

    # Cleave, Push, Sap, Slow, Vex — narrative effect + tracker hint
    tracker_hints = {
        "cleave": None,
        "push":   None,
        "sap":    "tracker: target → disadvantage on next attack (1 round)",
        "slow":   "tracker: target speed -10 ft until start of attacker's next turn",
        "vex":    "tracker: attacker → advantage on next attack vs this target (1 round)",
    }
    out["text"] = f"{prop.capitalize()} — {description}"
    if tracker_hints.get(prop):
        out["tracker_hint"] = tracker_hints[prop]
    return out


def list_masteries() -> str:
    """Print the full mastery property table — used by `combat.py masteries`."""
    lines = ["D&D 2024 Weapon Mastery Properties:", ""]
    for k in sorted(MASTERY_PROPERTIES):
        lines.append(f"  {k.capitalize():9s} {MASTERY_PROPERTIES[k]}")
    lines.append("")
    lines.append("Usage in combat:")
    lines.append("  python3 combat.py attack --atk N --ac N --dmg DICE --mastery <property>")
    lines.append("  python3 combat.py attack ... --mastery topple --topple-dc 14")
    lines.append("  python3 combat.py attack ... --mastery graze --ability-mod 4")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "init":
        combatants = json.loads(sys.argv[2])
        # Store max_hp
        for c in combatants:
            c["max_hp"] = c["hp"]
        ordered = initiative_order(combatants)
        print_tracker(ordered)
        print("Initiative rolls:")
        for c in ordered:
            print(f"  {c['name']}: d20({c['initiative_roll']}) + {c.get('dex_mod',0)} = {c['initiative']}")
        print()
        print("STATE_JSON:", json.dumps(ordered))

    elif cmd == "tracker":
        state = json.loads(sys.argv[2])
        round_num = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        print_tracker(state, round_num)

    elif cmd == "attack":
        args = sys.argv[2:]
        atk = int(args[args.index("--atk") + 1])
        ac = int(args[args.index("--ac") + 1])
        dmg = args[args.index("--dmg") + 1]
        crit = "--crit" in args
        result = resolve_attack(atk, ac, dmg, crit)

        # Optional 2024 weapon mastery
        if "--mastery" in args:
            mastery_name = args[args.index("--mastery") + 1]
            ability_mod = (int(args[args.index("--ability-mod") + 1])
                           if "--ability-mod" in args else 0)
            save_dc = (int(args[args.index("--topple-dc") + 1])
                       if "--topple-dc" in args else None)
            mastery = apply_mastery(mastery_name, result["hit"],
                                    ability_mod=ability_mod, save_dc=save_dc)
            result["mastery"]      = mastery
            result["mastery_text"] = mastery["text"]

        print(format_attack(result))

    elif cmd == "masteries":
        print(list_masteries())

    elif cmd == "mastery":
        # Ad-hoc mastery resolution (no attack roll context)
        args = sys.argv[2:]
        if not args:
            print("Usage: combat.py mastery <property> [--hit] [--ability-mod N] [--topple-dc N]")
            sys.exit(1)
        prop = args[0]
        hit  = "--hit" in args
        ability_mod = (int(args[args.index("--ability-mod") + 1])
                       if "--ability-mod" in args else 0)
        save_dc = (int(args[args.index("--topple-dc") + 1])
                   if "--topple-dc" in args else None)
        out = apply_mastery(prop, hit=hit, ability_mod=ability_mod,
                            save_dc=save_dc)
        print(json.dumps(out, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
