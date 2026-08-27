#!/usr/bin/env python3
"""
tracker.py — session-state tracker for conditions, concentration, effects, and death saves

Stores state per-campaign. Outputs formatted status to terminal and pushes
condition updates to the display companion sidebar (via push_stats.py).

Usage:
    CAMPAIGN=my-campaign

    # Conditions (5e SRD: blinded, charmed, deafened, exhausted, frightened,
    #   grappled, incapacitated, invisible, paralyzed, petrified, poisoned,
    #   prone, restrained, stunned, unconscious)
    python3 tracker.py -c $CAMPAIGN condition add <entity> <condition>
    python3 tracker.py -c $CAMPAIGN condition remove <entity> <condition>
    python3 tracker.py -c $CAMPAIGN condition clear <entity>

    # Concentration (one spell at a time per caster; auto-clears previous)
    python3 tracker.py -c $CAMPAIGN concentrate <entity> "<spell name>"
    python3 tracker.py -c $CAMPAIGN concentrate <entity> break

    # Timed effects — duration formats: 10r (rounds), 60m (minutes), 8h (hours), indef
    python3 tracker.py -c $CAMPAIGN effect start <entity> "<spell>" <duration> [conc]
    python3 tracker.py -c $CAMPAIGN effect end   <entity> "<spell>"
    python3 tracker.py -c $CAMPAIGN effect tick  <entity>   # call on that entity's turn start

    # Death saves (PC only — 3 successes = stable, 3 failures = dead)
    python3 tracker.py -c $CAMPAIGN saves <entity> success
    python3 tracker.py -c $CAMPAIGN saves <entity> failure
    python3 tracker.py -c $CAMPAIGN saves <entity> stable   # manually mark stable
    python3 tracker.py -c $CAMPAIGN saves <entity> reset    # regain consciousness

    # Status display
    python3 tracker.py -c $CAMPAIGN status [entity]   # all entities or one

    # Clear (end of encounter — wipes conditions/concentrations/effects; preserves saves)
    python3 tracker.py -c $CAMPAIGN clear [--all]     # --all also clears saves
"""

import json
import os
import sys
import subprocess
import argparse
import time
from datetime import datetime, timezone
from paths import find_campaign as _find_campaign, display_dir as _display_dir

PUSH_STATS = str(_display_dir() / "push_stats.py")
SEND_PY    = str(_display_dir() / "send.py")

# 5e condition colours for display pills
CONDITION_COLOURS = {
    "unconscious":    "danger",
    "paralyzed":      "danger",
    "petrified":      "danger",
    "stunned":        "danger",
    "incapacitated":  "warn",
    "frightened":     "warn",
    "poisoned":       "warn",
    "charmed":        "warn",
    "exhausted":      "warn",
    "grappled":       "info",
    "restrained":     "info",
    "prone":          "info",
    "blinded":        "info",
    "deafened":       "info",
    "invisible":      "buff",
}


def _state_path(campaign: str) -> str:
    d = str(_find_campaign(campaign))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "tracker.json")


def _load(campaign: str) -> dict:
    path = _state_path(campaign)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(campaign: str, state: dict) -> None:
    with open(_state_path(campaign), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _entity(state: dict, name: str) -> dict:
    """Get or create an entity entry."""
    key = name.lower()
    if key not in state:
        state[key] = {
            "name": name,
            "conditions": [],
            "concentration": None,
            "effects": [],
            "death_saves": {"successes": 0, "failures": 0, "stable": False},
        }
    # Backfill effects key for older entries
    state[key].setdefault("effects", [])
    return state[key]


def _parse_duration(dur_str: str) -> "dict | None":
    """Parse a duration string into an effect dict fragment.
    Formats: 10r (rounds), 60m (minutes), 8h (hours), indef (indefinite).
    Returns None on bad input.
    """
    d = dur_str.strip().lower()
    if d.endswith("r"):
        try:
            return {"duration_type": "rounds", "duration_remaining": int(d[:-1])}
        except ValueError:
            return None
    elif d.endswith("m"):
        try:
            secs = int(d[:-1]) * 60
            return {"duration_type": "minutes", "duration_seconds": secs, "started_at": time.time()}
        except ValueError:
            return None
    elif d.endswith("h"):
        try:
            secs = int(d[:-1]) * 3600
            return {"duration_type": "hours", "duration_seconds": secs, "started_at": time.time()}
        except ValueError:
            return None
    elif d == "indef":
        return {"duration_type": "indefinite"}
    return None


def _fmt_effect(eff: dict) -> str:
    """Return a short human-readable remaining-duration string."""
    dt = eff.get("duration_type", "indefinite")
    if dt == "rounds":
        r = eff.get("duration_remaining", 0)
        return f"{r} rnd"
    elif dt in ("minutes", "hours"):
        elapsed = time.time() - eff.get("started_at", time.time())
        remaining = max(0, eff.get("duration_seconds", 0) - elapsed)
        if remaining <= 0:
            return "expired"
        m, s = divmod(int(remaining), 60)
        if m >= 60:
            h, m = divmod(m, 60)
            return f"{h}h {m}m"
        return f"{m}:{s:02d}"
    return "∞"


def _push_conditions(entity_name: str, conditions: list[str]) -> None:
    """Push condition list to display sidebar via push_stats.py."""
    cond_str = ",".join(conditions)
    try:
        subprocess.run(
            [sys.executable, PUSH_STATS, "--player", entity_name,
             "--conditions", cond_str],
            capture_output=True, timeout=3,
        )
    except Exception:
        pass  # Display not running — fail silently


def _send_announce(msg: str) -> None:
    """Send a one-line announcement to the display companion."""
    try:
        proc = subprocess.Popen(
            [sys.executable, SEND_PY, "--dice"],
            stdin=subprocess.PIPE, capture_output=True, timeout=3,
        )
        proc.communicate(input=msg.encode())
    except Exception:
        pass


# ─── Commands ────────────────────────────────────────────────────────────────

def cmd_effect(campaign: str, action: str, entity_name: str,
               spell: str = "", duration: str = "", is_conc: bool = False) -> None:
    state = _load(campaign)
    ent   = _entity(state, entity_name)

    if action == "start":
        if not spell or not duration:
            print("  error: effect start requires <entity> <spell> <duration>")
            return
        dur = _parse_duration(duration)
        if dur is None:
            print(f"  error: bad duration '{duration}' — use 10r / 60m / 8h / indef")
            return

        effect = {"name": spell, "concentration": is_conc, **dur}

        # Replace any existing effect with same name
        ent["effects"] = [e for e in ent["effects"] if e["name"].lower() != spell.lower()]
        ent["effects"].append(effect)

        # Mirror concentration into the concentrate field
        if is_conc:
            old = ent.get("concentration")
            ent["concentration"] = spell
            if old and old.lower() != spell.lower():
                print(f"  {entity_name}: dropped concentration on '{old}'")
            _send_announce(f"{entity_name} — concentrating on {spell}")

        rem = _fmt_effect(effect)
        conc_tag = " [conc]" if is_conc else ""
        print(f"  + {entity_name}: {spell}{conc_tag} · {rem}")
        _save(campaign, state)

    elif action == "end":
        if not spell:
            print("  error: effect end requires <entity> <spell>")
            return
        before = len(ent["effects"])
        removed = [e for e in ent["effects"] if e["name"].lower() == spell.lower()]
        ent["effects"] = [e for e in ent["effects"] if e["name"].lower() != spell.lower()]
        if not removed:
            print(f"  ({entity_name} has no active effect '{spell}')")
            _save(campaign, state)
            return
        was_conc = any(e.get("concentration") for e in removed)
        if was_conc and (ent.get("concentration") or "").lower() == spell.lower():
            ent["concentration"] = None
            _send_announce(f"{entity_name} — concentration on {spell} ends")
            print(f"  - {entity_name}: {spell} ends (concentration broken)")
        else:
            _send_announce(f"{entity_name} — {spell} ends")
            print(f"  - {entity_name}: {spell} ends")
        _save(campaign, state)

    elif action == "tick":
        # Advance one round for this entity — decrement rounds, flag expired
        effects = ent.get("effects", [])
        kept, expired = [], []
        time_expired = []

        for e in effects:
            dt = e.get("duration_type")
            if dt == "rounds":
                e = dict(e)
                e["duration_remaining"] = max(0, e.get("duration_remaining", 1) - 1)
                if e["duration_remaining"] <= 0:
                    expired.append(e)
                else:
                    kept.append(e)
            elif dt in ("minutes", "hours"):
                elapsed = time.time() - e.get("started_at", time.time())
                remaining = e.get("duration_seconds", 0) - elapsed
                if remaining <= 0:
                    time_expired.append(e)
                else:
                    kept.append(e)
            else:
                kept.append(e)

        all_expired = expired + time_expired
        ent["effects"] = kept

        if not all_expired and not kept:
            # Nothing to report
            _save(campaign, state)
            return

        # Print tick summary
        if kept:
            lines = [f"⧗ {e['name']} · {_fmt_effect(e)}" for e in kept]
            print(f"  {entity_name} — effects: {', '.join(lines)}")

        for e in all_expired:
            was_conc = e.get("concentration", False)
            spell_name = e["name"]
            if was_conc and (ent.get("concentration") or "").lower() == spell_name.lower():
                ent["concentration"] = None
                _send_announce(f"{entity_name} — {spell_name} expired (concentration ends)")
                print(f"  ! {entity_name}: {spell_name} EXPIRED — concentration ends")
            else:
                _send_announce(f"{entity_name} — {spell_name} expired")
                print(f"  ! {entity_name}: {spell_name} EXPIRED")

        _save(campaign, state)

    else:
        print(f"  error: unknown effect action '{action}' — use start / end / tick")


def cmd_condition(campaign: str, entity_name: str, action: str, condition: str = "") -> None:
    state = _load(campaign)
    ent   = _entity(state, entity_name)
    conds = ent["conditions"]

    if action == "add":
        cond = condition.lower()
        if cond not in conds:
            conds.append(cond)
            _push_conditions(entity_name, conds)
            _send_announce(f"{entity_name} → {cond.capitalize()}")
            print(f"  + {entity_name}: {cond}")
        else:
            print(f"  (already has {cond})")

    elif action == "remove":
        cond = condition.lower()
        if cond in conds:
            conds.remove(cond)
            _push_conditions(entity_name, conds)
            _send_announce(f"{entity_name} — {cond.capitalize()} ends")
            print(f"  - {entity_name}: {cond} removed")
        else:
            print(f"  ({entity_name} does not have {cond})")

    elif action == "clear":
        if conds:
            _send_announce(f"{entity_name} — all conditions cleared")
        conds.clear()
        _push_conditions(entity_name, [])
        print(f"  {entity_name}: conditions cleared")

    ent["conditions"] = conds
    _save(campaign, state)


def cmd_concentrate(campaign: str, entity_name: str, spell_or_break: str) -> None:
    state = _load(campaign)
    ent   = _entity(state, entity_name)

    if spell_or_break.lower() == "break":
        old = ent.get("concentration")
        ent["concentration"] = None
        if old:
            _send_announce(f"{entity_name} — concentration on {old} broken")
            print(f"  {entity_name}: concentration on '{old}' broken")
        else:
            print(f"  {entity_name}: was not concentrating")
    else:
        spell = spell_or_break
        old   = ent.get("concentration")
        ent["concentration"] = spell
        if old and old != spell:
            _send_announce(f"{entity_name} — {old} ends, concentrating on {spell}")
            print(f"  {entity_name}: dropped '{old}', now concentrating on '{spell}'")
        else:
            _send_announce(f"{entity_name} — concentrating on {spell}")
            print(f"  {entity_name}: concentrating on '{spell}'")

    _save(campaign, state)


def cmd_saves(campaign: str, entity_name: str, action: str) -> None:
    state = _load(campaign)
    ent   = _entity(state, entity_name)
    saves = ent["death_saves"]

    if action == "success":
        saves["successes"] = min(3, saves["successes"] + 1)
        if saves["successes"] >= 3:
            saves["stable"] = True
            msg = f"{entity_name} is STABLE (3 successes)"
            _send_announce(f"☑ {msg}")
            print(f"  ☑ {msg}")
        else:
            print(f"  {entity_name}: {saves['successes']} success(es), {saves['failures']} failure(s)")

    elif action == "failure":
        saves["failures"] = min(3, saves["failures"] + 1)
        if saves["failures"] >= 3:
            msg = f"{entity_name} — 3 FAILURES: character is DEAD"
            _send_announce(f"✗ {msg}")
            print(f"  ✗ {msg}")
        else:
            print(f"  {entity_name}: {saves['successes']} success(es), {saves['failures']} failure(s)")

    elif action == "stable":
        saves["stable"] = True
        _send_announce(f"{entity_name} stabilised")
        print(f"  {entity_name}: marked stable")

    elif action == "reset":
        saves.update({"successes": 0, "failures": 0, "stable": False})
        print(f"  {entity_name}: death saves reset (regained consciousness)")

    ent["death_saves"] = saves
    _save(campaign, state)


def cmd_status(campaign: str, filter_name: str = "") -> None:
    state = _load(campaign)
    if not state:
        print("  (no tracked entities)")
        return

    entities = list(state.values())
    if filter_name:
        entities = [e for e in entities if filter_name.lower() in e["name"].lower()]

    for ent in entities:
        name   = ent["name"]
        conds  = ent.get("conditions", [])
        conc   = ent.get("concentration")
        saves  = ent.get("death_saves", {})

        effects = ent.get("effects", [])
        parts = []
        if conds:
            parts.append("Conditions: " + ", ".join(c.capitalize() for c in conds))
        if conc:
            parts.append(f"Concentrating: {conc}")
        if effects:
            eff_strs = []
            for e in effects:
                rem = _fmt_effect(e)
                tag = " [conc]" if e.get("concentration") else ""
                eff_strs.append(f"{e['name']}{tag} · {rem}")
            parts.append("Effects: " + ", ".join(eff_strs))
        s = saves.get("successes", 0)
        f = saves.get("failures", 0)
        if s or f:
            stable = " (stable)" if saves.get("stable") else ""
            parts.append(f"Death saves: {s}✓ {f}✗{stable}")

        if parts:
            print(f"  {name}:")
            for p in parts:
                print(f"    {p}")
        else:
            print(f"  {name}: clean")


def cmd_clear(campaign: str, clear_all: bool = False) -> None:
    state = _load(campaign)
    for ent in state.values():
        ent["conditions"] = []
        ent["concentration"] = None
        ent["effects"] = []
        _push_conditions(ent["name"], [])
        if clear_all:
            ent["death_saves"] = {"successes": 0, "failures": 0, "stable": False}
    _save(campaign, state)
    scope = "all state" if clear_all else "conditions and concentration"
    print(f"  Cleared {scope} for {len(state)} entities.")


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Session condition/death-save tracker.")
    p.add_argument("-c", "--campaign", required=True, metavar="NAME",
                   help="Campaign name (matches ~/.claude/dnd/campaigns/<name>/)")
    sub = p.add_subparsers(dest="cmd")

    # effect
    eff = sub.add_parser("effect", help="Track timed effects (rounds/minutes/hours/indefinite)")
    eff.add_argument("action", choices=["start", "end", "tick"])
    eff.add_argument("entity")
    eff.add_argument("spell", nargs="?", default="",
                     help="Spell or ability name")
    eff.add_argument("duration", nargs="?", default="",
                     help="Duration: 10r / 60m / 8h / indef")
    eff.add_argument("conc", nargs="?", default="",
                     help="Pass 'conc' to mark as concentration")

    # condition
    cond = sub.add_parser("condition", help="Add/remove/clear conditions")
    cond.add_argument("action", choices=["add", "remove", "clear"])
    cond.add_argument("entity")
    cond.add_argument("condition", nargs="?", default="")

    # concentrate
    conc = sub.add_parser("concentrate", help="Track spell concentration")
    conc.add_argument("entity")
    conc.add_argument("spell", help="Spell name, or 'break' to end concentration")

    # saves
    sav = sub.add_parser("saves", help="Track death saving throws")
    sav.add_argument("entity")
    sav.add_argument("action", choices=["success", "failure", "stable", "reset"])

    # status
    stat = sub.add_parser("status", help="Show current tracking state")
    stat.add_argument("entity", nargs="?", default="")

    # clear
    clr = sub.add_parser("clear", help="Clear encounter state")
    clr.add_argument("--all", action="store_true",
                     help="Also clear death saves (not just conditions/concentration)")

    args = p.parse_args()

    if args.cmd == "effect":
        cmd_effect(args.campaign, args.action, args.entity,
                   spell=args.spell, duration=args.duration,
                   is_conc=(args.conc.lower() == "conc"))
    elif args.cmd == "condition":
        cmd_condition(args.campaign, args.entity, args.action, args.condition)
    elif args.cmd == "concentrate":
        cmd_concentrate(args.campaign, args.entity, args.spell)
    elif args.cmd == "saves":
        cmd_saves(args.campaign, args.entity, args.action)
    elif args.cmd == "status":
        cmd_status(args.campaign, args.entity)
    elif args.cmd == "clear":
        cmd_clear(args.campaign, getattr(args, "all", False))
    else:
        p.print_help()


if __name__ == "__main__":
    main()
