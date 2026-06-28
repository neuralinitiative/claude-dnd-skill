"""
oracle.py — solo/GM oracle tools (chaos factor, yes/no, event focus, scene meaning).

Ported from the Neural Initiative engine's oracle module, re-implemented in the
skill's idiom (stdlib only; chaos factor persisted in the campaign's state.md
Session Flags rather than a DB row).

These are deterministic dice-driven tools, not LLM prompts. Wiring them in as
explicit commands stops the model from inventing its own pacing and gives the
table a transparent, rollable backbone for improvised play.

References:
  - Mythic GME 2e (Word Mill Games): chaos factor + Random Event Focus.
  - Ironsworn (Shawn Tomkin, CC-BY-4.0): yes/no oracle shape.
  - One Page Solo Engine: random scene-meaning word pairs.

Rolls use stdlib `random` so results are seedable for tests. `scripts/dice.py`
is available if you want physical-dice routing for the underlying d100, but the
oracle math stays here so verdicts are reproducible.

CLI:
  python3 oracle.py chaos        --campaign NAME            # show current factor
  python3 oracle.py chaos set    --campaign NAME --value N  # set factor (1-9)
  python3 oracle.py chaos adjust --campaign NAME --pc-won|--pc-lost
  python3 oracle.py ask  [--likelihood L] [--campaign NAME | --chaos N] [--seed S]
  python3 oracle.py event [--seed S]
  python3 oracle.py scene [--seed S]
"""
from __future__ import annotations

import argparse
import random
import re
import sys

try:
    from paths import find_campaign
except ImportError:  # pragma: no cover
    find_campaign = None  # type: ignore


# ── chaos factor (Mythic-style, 1-9) ─────────────────────────────────────────

CHAOS_MIN, CHAOS_MAX, CHAOS_DEFAULT = 1, 9, 5
_CHAOS_FLAG_RE = re.compile(r"^(\s*[-*]?\s*)chaos_factor:\s*([0-9]+)\s*$",
                            re.IGNORECASE)


def clamp_chaos(n: int) -> int:
    return max(CHAOS_MIN, min(CHAOS_MAX, n))


def adjust_chaos(factor: int, pc_proactive: bool) -> int:
    """Standard Mythic move: PC achieved scene goal → -1 (more in control);
    PC was reactive / failed → +1 (world pushes back). Clamped to [1, 9]."""
    return clamp_chaos(factor + (-1 if pc_proactive else 1))


def read_chaos(state_text: str) -> int:
    """Read `chaos_factor: N` from a state.md body. Default 5 if unset."""
    for line in state_text.splitlines():
        m = _CHAOS_FLAG_RE.match(line)
        if m:
            return clamp_chaos(int(m.group(2)))
    return CHAOS_DEFAULT


def write_chaos(state_text: str, value: int) -> str:
    """Return state.md text with `chaos_factor: N` set under ## Session Flags.

    Updates the line in place if present; otherwise inserts it just after the
    `## Session Flags` heading. If there is no Session Flags section, appends
    one at the end.
    """
    value = clamp_chaos(value)
    lines = state_text.splitlines()

    # In-place update if the flag already exists.
    for i, line in enumerate(lines):
        m = _CHAOS_FLAG_RE.match(line)
        if m:
            lines[i] = f"{m.group(1)}chaos_factor: {value}"
            return "\n".join(lines) + ("\n" if state_text.endswith("\n") else "")

    # Insert under an existing Session Flags heading.
    for i, line in enumerate(lines):
        if re.match(r"^##\s+Session Flags\s*$", line, re.IGNORECASE):
            lines.insert(i + 1, f"- chaos_factor: {value}")
            return "\n".join(lines) + ("\n" if state_text.endswith("\n") else "")

    # No section — append one.
    suffix = "" if state_text.endswith("\n") else "\n"
    return (state_text + suffix +
            f"\n## Session Flags\n- chaos_factor: {value}\n")


# ── yes/no oracle (Ironsworn-shaped) ─────────────────────────────────────────

_LIKELIHOODS = {
    "sure-thing": 90,
    "likely": 75,
    "50/50": 50,
    "unlikely": 25,
    "no-way": 10,
}


def yes_no(likelihood: str = "50/50", chaos: int = CHAOS_DEFAULT,
           rng: "random.Random | None" = None) -> "tuple[str, int]":
    """Return (verdict, roll). Verdict ∈ {yes, yes-and, yes-but, no, no-but, no-and}.

    Likelihood sets the base d100 target; chaos shifts it (high chaos widens the
    odds the world swings). Doubles → '-and' (extreme); within 10 of target →
    '-but' (qualified).
    """
    rng = rng or random.Random()
    base = _LIKELIHOODS.get(likelihood, 50)
    target = max(5, min(95, base + (chaos - CHAOS_DEFAULT) * 2))
    roll = rng.randint(1, 100)
    is_yes = roll <= target

    diff = abs(roll - target)
    rs = str(roll).zfill(2)
    if rs[0] == rs[1]:
        modifier = "-and"
    elif diff < 10:
        modifier = "-but"
    else:
        modifier = ""

    return ("yes" if is_yes else "no") + modifier, roll


# ── Random Event Focus (Mythic d100) ─────────────────────────────────────────

_FOCUS_TABLE = [
    (5, "remote event"),
    (10, "ambiguous event"),
    (20, "new NPC"),
    (35, "NPC action"),
    (45, "introduce thread"),
    (55, "move toward thread"),
    (65, "move away from thread"),
    (70, "close thread"),
    (80, "PC negative"),
    (85, "PC positive"),
    (95, "ambiguous event"),
    (100, "current context"),
]


def random_event_focus(rng: "random.Random | None" = None) -> "tuple[int, str]":
    """Mythic Random Event Focus. Returns (roll, focus_label). Treat the label
    as a *direction* to interpret against current threads, NPCs, and places."""
    rng = rng or random.Random()
    roll = rng.randint(1, 100)
    for threshold, label in _FOCUS_TABLE:
        if roll <= threshold:
            return roll, label
    return roll, "current context"


# ── scene-meaning word pairs (One Page Solo Engine) ──────────────────────────

_ACTIONS = [
    "abandon", "agree", "ambush", "approach", "arrive", "assist", "attach",
    "attain", "attract", "betray", "bind", "block", "break", "buy", "carry",
    "celebrate", "change", "command", "communicate", "conceal", "conclude",
    "construct", "contest", "create", "darken", "decay", "decrease", "defeat",
    "depart", "deprive", "destroy", "discover", "disturb", "dominate",
    "encourage", "endure", "energize", "equip", "expose", "fail", "fight",
    "flee", "forget", "forgive", "free", "gather", "give", "gladden",
    "guard", "guide", "harm", "heal", "hide", "hinder", "honour", "imitate",
    "increase", "inquire", "judge", "kill", "lead", "leave", "lighten",
    "love", "manipulate", "negate", "obey", "offer", "open", "oppose",
    "overcome", "pacify", "persecute", "praise", "preserve", "prevent",
    "pretend", "promise", "punish", "pursue", "reduce", "refuse", "release",
    "remember", "remove", "repulse", "reveal", "ruin", "save", "scheme",
    "seize", "share", "soothe", "spy", "start", "steal", "stop",
    "succeed", "suspect", "swear", "take", "threaten", "transform", "trick",
    "trust", "uncover", "undermine", "unify", "use", "venture",
    "violate", "wait", "warn", "weaken", "win", "wish", "wound", "yield",
]
_SUBJECTS = [
    "advice", "agreement", "animal", "armament", "art", "attention",
    "balance", "battle", "beauty", "benefit", "blood", "body", "bond",
    "book", "burden", "ceremony", "chaos", "child", "clothing", "code",
    "community", "competition", "conflict", "creation", "crime",
    "danger", "darkness", "death", "decision", "deed", "destiny", "device",
    "dialogue", "disaster", "disease", "doorway", "dream", "duty", "edge",
    "element", "emotion", "energy", "enemy", "entrance", "expectation",
    "expedition", "eye", "factor", "family", "famine", "fate", "fear",
    "festival", "fire", "fortune", "freedom", "friend", "garden", "gift",
    "gold", "good", "government", "health", "history", "home", "honour",
    "hope", "humour", "idea", "illusion", "image", "industry", "injustice",
    "intelligence", "investment", "joy", "justice", "key", "knowledge",
    "labour", "land", "language", "law", "lie", "life", "light", "luck",
    "magic", "marriage", "maze", "memory", "message", "messenger", "metal",
    "military", "mind", "mistake", "money", "music", "nature", "object",
    "oddity", "opposition", "outsider", "passage", "path", "peace", "people",
    "personality", "place", "plot", "ploy", "portent", "possessions",
    "poverty", "power", "project", "protection", "reality", "religion",
    "reputation", "resource", "rumour", "ruse", "sacrifice", "secret",
    "shadow", "shelter", "ship", "shore", "sign", "skill", "spirit",
    "stalemate", "structure", "success", "suffering", "surprise", "symbol",
    "task", "tension", "test", "thought", "time", "title", "tool",
    "transformation", "trap", "travel", "treasure", "trial", "trick",
    "truce", "truth", "unity", "value", "vehicle", "victory", "virtue",
    "void", "vow", "war", "warning", "way", "weapon", "weather", "wisdom",
    "wish", "world", "wound", "youth",
]


def scene_meaning(rng: "random.Random | None" = None) -> "tuple[str, str]":
    """Two-word generator: (action_verb, subject_noun). Feed to the narration
    when a scene runs dry or a Random Event Focus rolls."""
    rng = rng or random.Random()
    return rng.choice(_ACTIONS), rng.choice(_SUBJECTS)


# ── state.md helpers ─────────────────────────────────────────────────────────


def _resolve_state(name: str):
    if find_campaign is None:
        raise RuntimeError("paths.find_campaign unavailable; run from skill scripts dir")
    return find_campaign(name) / "state.md"


# ── CLI ──────────────────────────────────────────────────────────────────────


def cmd_chaos(args) -> int:
    state_path = _resolve_state(args.campaign)
    text = state_path.read_text(encoding="utf-8") if state_path.exists() else ""
    current = read_chaos(text)

    if args.chaos_action == "set":
        new = clamp_chaos(args.value)
        state_path.write_text(write_chaos(text, new), encoding="utf-8")
        print(f"chaos factor: {current} → {new}")
        return 0
    if args.chaos_action == "adjust":
        if args.pc_won == args.pc_lost:
            print("error: pass exactly one of --pc-won / --pc-lost", file=sys.stderr)
            return 2
        new = adjust_chaos(current, pc_proactive=args.pc_won)
        state_path.write_text(write_chaos(text, new), encoding="utf-8")
        verb = "PC in control" if args.pc_won else "world pushes back"
        print(f"chaos factor: {current} → {new}  ({verb})")
        return 0
    # default: show
    print(f"chaos factor: {current}  (1=PCs in control, 9=world in chaos)")
    return 0


def _chaos_for(args) -> int:
    """Resolve a chaos value for ask: explicit --chaos wins, else read campaign,
    else neutral default."""
    if args.chaos is not None:
        return clamp_chaos(args.chaos)
    if args.campaign:
        state_path = _resolve_state(args.campaign)
        if state_path.exists():
            return read_chaos(state_path.read_text(encoding="utf-8"))
    return CHAOS_DEFAULT


def cmd_ask(args) -> int:
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    chaos = _chaos_for(args)
    verdict, roll = yes_no(args.likelihood, chaos, rng=rng)
    print(f"{verdict.upper()}  (d100={roll}, likelihood={args.likelihood}, chaos={chaos})")
    return 0


def cmd_event(args) -> int:
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    roll, label = random_event_focus(rng=rng)
    print(f"event focus: {label}  (d100={roll})")
    return 0


def cmd_scene(args) -> int:
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    action, subject = scene_meaning(rng=rng)
    print(f"scene meaning: {action} / {subject}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="oracle", description=__doc__.split("\n", 2)[1])
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("chaos", help="get/set/adjust the campaign chaos factor (1-9)")
    sp.add_argument("chaos_action", nargs="?", choices=["show", "set", "adjust"],
                    default="show")
    sp.add_argument("--campaign", required=True)
    sp.add_argument("--value", type=int, help="for `set`: the new factor (1-9)")
    sp.add_argument("--pc-won", action="store_true",
                    help="for `adjust`: PC achieved the scene goal → -1")
    sp.add_argument("--pc-lost", action="store_true",
                    help="for `adjust`: PC was reactive / failed → +1")
    sp.set_defaults(func=cmd_chaos)

    sp = sub.add_parser("ask", help="yes/no oracle (Ironsworn-shaped)")
    sp.add_argument("--likelihood", default="50/50", choices=list(_LIKELIHOODS),
                    help="odds before chaos modifier")
    sp.add_argument("--campaign", help="read chaos factor from this campaign")
    sp.add_argument("--chaos", type=int, help="explicit chaos factor (overrides --campaign)")
    sp.add_argument("--seed", type=int, help="seed the d100 (reproducible)")
    sp.set_defaults(func=cmd_ask)

    sp = sub.add_parser("event", help="Mythic Random Event Focus (d100)")
    sp.add_argument("--seed", type=int)
    sp.set_defaults(func=cmd_event)

    sp = sub.add_parser("scene", help="random scene-meaning word pair")
    sp.add_argument("--seed", type=int)
    sp.set_defaults(func=cmd_scene)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
