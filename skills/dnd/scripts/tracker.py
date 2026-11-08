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
                       [--mod +1d4 --on attack,save]   # a typed modifier (Bless)
    python3 tracker.py -c $CAMPAIGN effect end   <entity> "<spell>"
    python3 tracker.py -c $CAMPAIGN effect tick  <entity>   # call on that entity's turn start
    python3 tracker.py -c $CAMPAIGN effect advance <time>   # in-game time passes: 90 / 90m / 8h / 10r

    # Durations run on IN-GAME time. Nothing expires because the session ran
    # long or the laptop slept; time moves only when a turn ticks, when
    # `effect advance` is run, or when calendar.py advances / rests.

    # Rests — advance in-game time and apply the rest's bookkeeping
    python3 tracker.py -c $CAMPAIGN rest short   # +1 hour; effects keep running
    python3 tracker.py -c $CAMPAIGN rest long    # +8 hours; concentration ends,
                                                 # exhaustion -1 (not at 0 HP)

    # Death saves (PC only — 3 successes = stable, 3 failures = dead)
    python3 tracker.py -c $CAMPAIGN saves <entity> success
    python3 tracker.py -c $CAMPAIGN saves <entity> failure
    python3 tracker.py -c $CAMPAIGN saves <entity> stable   # manually mark stable
    python3 tracker.py -c $CAMPAIGN saves <entity> reset    # regain consciousness

    # Status display
    python3 tracker.py -c $CAMPAIGN status [entity]   # all entities or one

    # Clear (end of encounter — wipes encounter conditions and round-long
    # effects; keeps minute/hour effects, exhaustion and petrification)
    python3 tracker.py -c $CAMPAIGN clear [--all]     # --all also resets death
                                                      # saves (never a dead PC's)
"""

import json
import math
import re
import os
import sys
import subprocess
import argparse
import time
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

#: Conditions an encounter ending does not end. Exhaustion is removed one
#: level per long rest and petrification by magic; neither wears off because
#: the fight is over, so `clear` (and `clear --all`) leave them in place.
LASTING_CONDITIONS = {"exhausted", "petrified"}

#: Roll types a tracked modifier can apply to (`effect start --on`).
MODIFIER_TARGETS = ("attack", "save", "check", "damage")

#: One combat round is six seconds of in-game time.
MINUTES_PER_ROUND = 0.1


def _state_path(campaign: str) -> str:
    d = str(_find_campaign(campaign))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "tracker.json")


def _load(campaign: str) -> dict:
    path = _state_path(campaign)
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    for ent in state.values():
        ent["effects"] = [_migrate_effect(e) for e in ent.get("effects", [])]
    return state


def _migrate_effect(eff: dict) -> dict:
    """Convert a wall-clock effect from an older tracker.json to in-game time.

    Minute and hour effects used to store `started_at=time.time()` and expire
    on the real clock, so an 8-hour Mage Armor ran out after eight real hours
    whatever happened in the fiction. An existing entry keeps the remaining
    time it showed at the moment of the update; from then on only in-game
    time moves it.
    """
    if eff.get("duration_type") not in ("minutes", "hours") or "remaining_minutes" in eff:
        return eff
    eff = dict(eff)
    secs = eff.pop("duration_seconds", 0)
    started = eff.pop("started_at", None)
    elapsed = (time.time() - started) if started else 0
    eff["remaining_minutes"] = max(0.0, (secs - elapsed) / 60.0)
    return eff


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
    # Backfill keys for older entries
    state[key].setdefault("effects", [])
    state[key].setdefault("conditions", [])
    state[key].setdefault("death_saves", {"successes": 0, "failures": 0, "stable": False})
    if "exhaustion" not in state[key]:
        state[key]["exhaustion"] = 1 if "exhausted" in state[key]["conditions"] else 0
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
            return {"duration_type": "minutes", "remaining_minutes": float(int(d[:-1]))}
        except ValueError:
            return None
    elif d.endswith("h"):
        try:
            return {"duration_type": "hours", "remaining_minutes": float(int(d[:-1]) * 60)}
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
        remaining = eff.get("remaining_minutes", 0)
        if remaining <= 1e-9:
            return "expired"
        if remaining < 1:
            return f"{math.ceil(round(remaining / MINUTES_PER_ROUND, 6))} rnd"
        m = math.ceil(round(remaining, 6))
        if m >= 60:
            h, m = divmod(m, 60)
            return f"{h}h {m}m"
        return f"{m}m"
    return "∞"


def _fmt_modifier(eff: dict) -> str:
    mod = eff.get("modifier")
    if not mod:
        return ""
    return f" · {mod['value']} {'/'.join(mod.get('applies', []))}"


def parse_time(spec: str) -> "float | None":
    """In-game time for `effect advance`: 90 / 90m (minutes), 8h, 10r (rounds)."""
    d = (spec or "").strip().lower()
    try:
        if d.endswith("h"):
            return float(d[:-1]) * 60
        if d.endswith("r"):
            return float(d[:-1]) * MINUTES_PER_ROUND
        if d.endswith("m"):
            return float(d[:-1])
        return float(d)
    except ValueError:
        return None


def _run_down(ent: dict, minutes: float) -> list:
    """Spend `minutes` of in-game time on one entity's effects.

    Returns the effects that ran out; the survivors stay on the entity. A
    round-counted effect loses ten rounds per minute, so a 10-round Bless
    does not outlast an hour of travel.
    """
    kept, expired = [], []
    for e in ent.get("effects", []):
        dt = e.get("duration_type")
        if dt == "rounds":
            e = dict(e)
            rounds = int(round(minutes / MINUTES_PER_ROUND))
            e["duration_remaining"] = max(0, e.get("duration_remaining", 1) - rounds)
            (expired if e["duration_remaining"] <= 0 else kept).append(e)
        elif dt in ("minutes", "hours"):
            e = dict(e)
            e["remaining_minutes"] = max(0.0, e.get("remaining_minutes", 0) - minutes)
            (expired if e["remaining_minutes"] <= 1e-9 else kept).append(e)
        else:
            kept.append(e)
    ent["effects"] = kept
    return expired


def _report_expired(ent: dict, expired: list) -> None:
    entity_name = ent["name"]
    for e in expired:
        spell_name = e["name"]
        if e.get("concentration") and (ent.get("concentration") or "").lower() == spell_name.lower():
            ent["concentration"] = None
            _send_announce(f"{entity_name} — {spell_name} expired (concentration ends)")
            print(f"  ! {entity_name}: {spell_name} EXPIRED — concentration ends")
        else:
            _send_announce(f"{entity_name} — {spell_name} expired")
            print(f"  ! {entity_name}: {spell_name} EXPIRED")


def advance(campaign: str, minutes: float, quiet: bool = False) -> list:
    """Advance in-game time for every tracked entity. Returns [(entity, effect)].

    Called by `effect advance`, by `rest`, and by calendar.py when it moves
    the clock, so a journey or a rest runs effects down by the time it took.
    """
    state = _load(campaign)
    out = []
    for ent in state.values():
        expired = _run_down(ent, minutes)
        if not quiet:
            _report_expired(ent, expired)
        out.extend((ent["name"], e) for e in expired)
    if state:
        _save(campaign, state)
    return out


def active_modifiers(campaign: str, entity_name: str, roll_type: str) -> list:
    """[(label, '+1d4'), ...] for the entity's effects that apply to roll_type.

    dice.py and combat.py call this for `--effects <entity>`, so a Bless that
    tracker.py holds is added to the roll instead of being remembered.
    """
    ent = _load(campaign).get(entity_name.lower())
    if not ent:
        return []
    out = []
    for e in ent.get("effects", []):
        mod = e.get("modifier")
        if mod and roll_type in mod.get("applies", []):
            out.append((e["name"], mod["value"]))
    return out


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
               spell: str = "", duration: str = "", is_conc: bool = False,
               mod: str = "", on: str = "") -> None:
    if action == "advance":
        # `effect advance <time>` — the time arrives in the entity slot.
        minutes = parse_time(entity_name)
        if minutes is None or minutes < 0:
            print(f"  error: bad time '{entity_name}' — use 90 / 90m / 8h / 10r")
            sys.exit(2)
        expired = advance(campaign, minutes)
        if not expired:
            print(f"  +{minutes:g} min in-game — nothing expired")
        return

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
        if mod:
            applies = [t.strip().lower() for t in (on or "").split(",") if t.strip()]
            bad = [t for t in applies if t not in MODIFIER_TARGETS]
            if not applies or bad:
                print(f"  error: --mod needs --on with any of {', '.join(MODIFIER_TARGETS)}")
                sys.exit(2)
            if not re.match(r"^[+-](\d*d\d+|\d+)$", mod.strip().lower()):
                print(f"  error: bad modifier '{mod}' — use +1d4 / -1d4 / +2")
                sys.exit(2)
            effect["modifier"] = {"value": mod.strip().lower(), "applies": applies}

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
        print(f"  + {entity_name}: {spell}{conc_tag} · {rem}{_fmt_modifier(effect)}")
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
        # One round passes for this entity: six seconds of in-game time, so a
        # round-counted effect loses a round and a minute effect 0.1 minute.
        all_expired = _run_down(ent, MINUTES_PER_ROUND)
        kept = ent["effects"]

        if not all_expired and not kept:
            # Nothing to report
            _save(campaign, state)
            return

        # Print tick summary
        if kept:
            lines = [f"⧗ {e['name']} · {_fmt_effect(e)}" for e in kept]
            print(f"  {entity_name} — effects: {', '.join(lines)}")

        _report_expired(ent, all_expired)
        _save(campaign, state)

    else:
        print(f"  error: unknown effect action '{action}' — use start / end / tick / advance")


def cmd_condition(campaign: str, entity_name: str, action: str, condition: str = "") -> None:
    state = _load(campaign)
    ent   = _entity(state, entity_name)
    conds = ent["conditions"]

    if action == "add" and condition.lower() in ("exhausted", "exhaustion"):
        # Exhaustion stacks in levels (6 is death), unlike every other
        # condition, so adding it again is another level rather than a no-op.
        ent["exhaustion"] = min(6, ent.get("exhaustion", 0) + 1)
        if "exhausted" not in conds:
            conds.append("exhausted")
            _push_conditions(entity_name, conds)
        _send_announce(f"{entity_name} → Exhaustion {ent['exhaustion']}")
        print(f"  + {entity_name}: exhaustion level {ent['exhaustion']}")
        if ent["exhaustion"] >= 6:
            print(f"  ✗ {entity_name}: exhaustion 6 — the character dies")

    elif action == "remove" and condition.lower() in ("exhausted", "exhaustion"):
        if ent.get("exhaustion", 0) <= 0:
            print(f"  ({entity_name} is not exhausted)")
        else:
            ent["exhaustion"] -= 1
            if ent["exhaustion"] == 0 and "exhausted" in conds:
                conds.remove("exhausted")
                _push_conditions(entity_name, conds)
            print(f"  - {entity_name}: exhaustion level {ent['exhaustion']}")

    elif action == "add":
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
        ent["exhaustion"] = 0
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
            lvl = ent.get("exhaustion", 0)
            parts.append("Conditions: " + ", ".join(
                f"Exhausted (level {lvl})" if c == "exhausted" and lvl else c.capitalize()
                for c in conds))
        if conc:
            parts.append(f"Concentrating: {conc}")
        if effects:
            eff_strs = []
            for e in effects:
                rem = _fmt_effect(e)
                tag = " [conc]" if e.get("concentration") else ""
                eff_strs.append(f"{e['name']}{tag} · {rem}{_fmt_modifier(e)}")
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


def _is_dead(ent: dict) -> bool:
    return ent.get("death_saves", {}).get("failures", 0) >= 3 or ent.get("exhaustion", 0) >= 6


def _is_down(ent: dict) -> bool:
    """At 0 HP as far as the tracker can tell: dying, stable, or unconscious."""
    s = ent.get("death_saves", {})
    return (_is_dead(ent) or s.get("stable") or s.get("successes", 0) or s.get("failures", 0)
            or "unconscious" in ent.get("conditions", []))


def cmd_clear(campaign: str, clear_all: bool = False) -> None:
    """End of encounter.

    This used to set effects=[] and drop every condition, and the rest
    procedures ran it too -- so a one-hour short rest deleted an eight-hour
    Mage Armor, and a long rest cleared exhaustion outright. It now ends what
    the encounter owned: conditions other than LASTING_CONDITIONS, effects
    counted in rounds, and concentration that no surviving effect holds.
    """
    state = _load(campaign)
    for ent in state.values():
        _entity(state, ent["name"])
        ent["conditions"] = [c for c in ent["conditions"] if c in LASTING_CONDITIONS]
        ent["effects"] = [e for e in ent["effects"] if e.get("duration_type") != "rounds"]
        held = {e["name"].lower() for e in ent["effects"] if e.get("concentration")}
        if (ent.get("concentration") or "").lower() not in held:
            ent["concentration"] = None
        _push_conditions(ent["name"], ent["conditions"])
        if clear_all and not _is_dead(ent):
            # A dead character's three failures are not encounter state.
            ent["death_saves"] = {"successes": 0, "failures": 0, "stable": False}
    _save(campaign, state)
    scope = "encounter state and death saves" if clear_all else "encounter state"
    print(f"  Cleared {scope} for {len(state)} entities "
          f"(minute/hour effects, exhaustion and petrification kept).")


REST_MINUTES = {"short": 60, "long": 480}


def cmd_rest(campaign: str, rest_type: str, advance_time: bool = True) -> None:
    """Rest bookkeeping for tracked entities. Time passes; nothing is wiped.

    Short: one hour passes. Long: eight hours pass, concentration ends
    (sleeping is unconsciousness), and exhaustion drops one level -- except
    for a creature at 0 HP, which gains no benefit from a long rest until it
    has at least 1 hit point.
    """
    if advance_time:
        advance(campaign, REST_MINUTES[rest_type])
    if rest_type != "long":
        print("  Short rest: 1 hour passed; effects kept running.")
        return
    state = _load(campaign)
    for ent in state.values():
        _entity(state, ent["name"])
        name = ent["name"]
        if ent.get("concentration"):
            spell = ent["concentration"]
            ent["concentration"] = None
            ent["effects"] = [e for e in ent["effects"]
                              if not (e.get("concentration") and e["name"].lower() == spell.lower())]
            print(f"  {name}: concentration on {spell} ends")
        if _is_dead(ent):
            continue
        if _is_down(ent):
            print(f"  {name}: at 0 HP — no long-rest benefit until they have at least 1 HP")
            continue
        if ent.get("exhaustion", 0) > 0:
            ent["exhaustion"] -= 1
            if ent["exhaustion"] == 0 and "exhausted" in ent["conditions"]:
                ent["conditions"].remove("exhausted")
                _push_conditions(name, ent["conditions"])
            print(f"  {name}: exhaustion {ent['exhaustion'] + 1} → {ent['exhaustion']}")
    _save(campaign, state)
    print("  Long rest: 8 hours passed.")


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Session condition/death-save tracker.")
    p.add_argument("-c", "--campaign", required=True, metavar="NAME",
                   help="Campaign name (matches ~/.claude/dnd/campaigns/<name>/)")
    sub = p.add_subparsers(dest="cmd")

    # effect
    eff = sub.add_parser("effect", help="Track timed effects (rounds/minutes/hours/indefinite)")
    eff.add_argument("action", choices=["start", "end", "tick", "advance"])
    eff.add_argument("entity", help="Entity name, or the time for `advance` (90 / 90m / 8h / 10r)")
    eff.add_argument("spell", nargs="?", default="",
                     help="Spell or ability name")
    eff.add_argument("duration", nargs="?", default="",
                     help="Duration: 10r / 60m / 8h / indef")
    eff.add_argument("conc", nargs="?", default="",
                     help="Pass 'conc' to mark as concentration")
    eff.add_argument("--mod", default="",
                     help="Typed modifier the effect grants, e.g. +1d4 (Bless) or -1d4 (Bane)")
    eff.add_argument("--on", default="",
                     help=f"Roll types the modifier applies to: {', '.join(MODIFIER_TARGETS)}")

    # rest
    rst = sub.add_parser("rest", help="Advance in-game time for a rest and apply its bookkeeping")
    rst.add_argument("type", choices=["short", "long"])

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
                     help="Also reset death saves (never a dead character's)")

    args = p.parse_args()

    if args.cmd == "effect":
        cmd_effect(args.campaign, args.action, args.entity,
                   spell=args.spell, duration=args.duration,
                   is_conc=(args.conc.lower() == "conc"),
                   mod=args.mod, on=args.on)
    elif args.cmd == "rest":
        cmd_rest(args.campaign, args.type)
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
