#!/usr/bin/env python3
"""
ability-scores.py — D&D 5e ability score generation

Modes:
    python3 ability-scores.py roll          Roll 4d6kh3 six times, print results
    python3 ability-scores.py pointbuy      Interactive point buy (27 points)
    python3 ability-scores.py pointbuy --check STR=15 DEX=10 CON=15 INT=8 WIS=11 CHA=12
                                            Validate and cost a given array
    python3 ability-scores.py modifiers STR=15 DEX=10 CON=15 INT=8 WIS=11 CHA=12
                                            Print modifiers for a given array

Point Buy cost table (D&D 5e standard):
    Score:  8   9  10  11  12  13  14  15
    Cost:   0   1   2   3   4   5   7   9
"""

import secrets
import sys


STATS = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]

POINT_BUY_COST = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
POINT_BUY_BUDGET = 27
POINT_BUY_MIN = 8
POINT_BUY_MAX = 15


def modifier(score: int) -> str:
    mod = (score - 10) // 2
    return f"+{mod}" if mod >= 0 else str(mod)


def roll_set() -> list[int]:
    """Roll 4d6 drop lowest, return six scores."""
    scores = []
    for _ in range(6):
        rolls = sorted([secrets.randbelow(6) + 1 for _ in range(4)], reverse=True)
        kept = rolls[:3]
        scores.append(sum(kept))
    return scores


def print_scores(scores: list[int], labels=None):
    labels = labels or STATS
    header = "  ".join(f"{l:>4}" for l in labels)
    values = "  ".join(f"{s:>4}" for s in scores)
    mods   = "  ".join(f"{modifier(s):>4}" for s in scores)
    print(f"  {header}")
    print(f"  {values}")
    print(f"  {mods}  (modifiers)")


def do_roll():
    print("Rolling ability scores: 4d6 drop lowest × 6\n")
    sets = []
    for i in range(3):
        scores = roll_set()
        total = sum(scores)
        sets.append((scores, total))

    print("Three arrays — pick one and assign scores to stats as you like:\n")
    for i, (scores, total) in enumerate(sets, 1):
        rolls_display = []
        for _ in range(6):
            # re-display what was rolled — just show the score
            pass
        sorted_scores = sorted(scores, reverse=True)
        print(f"  Array {i}:  {sorted_scores}  (total: {total})")
    print()
    print("To assign, tell the DM which array and which score goes to which stat.")


def do_pointbuy_check(assignments: dict[str, int]):
    """Validate and display cost of a given assignment."""
    errors = []
    total_cost = 0
    for stat, score in assignments.items():
        if score not in POINT_BUY_COST:
            errors.append(f"{stat}={score} is out of range ({POINT_BUY_MIN}–{POINT_BUY_MAX})")
        else:
            total_cost += POINT_BUY_COST[score]

    if errors:
        print("Errors:")
        for e in errors:
            print(f"  {e}")
        return

    budget_remaining = POINT_BUY_BUDGET - total_cost
    status = "OK" if budget_remaining == 0 else (f"UNDER by {budget_remaining}" if budget_remaining > 0 else f"OVER by {abs(budget_remaining)}")

    print(f"\nPoint Buy Validation")
    print(f"{'Stat':<6} {'Score':>6} {'Cost':>6} {'Mod':>6}")
    print("-" * 28)
    for stat in STATS:
        score = assignments.get(stat, 8)
        cost = POINT_BUY_COST.get(score, "?")
        print(f"{stat:<6} {score:>6} {str(cost):>6} {modifier(score):>6}")
    print("-" * 28)
    print(f"{'TOTAL':<6} {'':>6} {total_cost:>6}/27  {status}")


def do_modifiers(assignments: dict[str, int]):
    print(f"\n{'Stat':<6} {'Score':>6} {'Modifier':>9}")
    print("-" * 24)
    for stat in STATS:
        score = assignments.get(stat, 8)
        print(f"{stat:<6} {score:>6} {modifier(score):>9}")


def parse_assignments(args: list[str]) -> dict[str, int]:
    assignments = {}
    for arg in args:
        if "=" in arg:
            k, v = arg.split("=", 1)
            assignments[k.upper()] = int(v)
    return assignments


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    mode = sys.argv[1].lower()
    rest = sys.argv[2:]

    if mode == "roll":
        do_roll()

    elif mode == "pointbuy":
        if "--check" in rest:
            rest = [r for r in rest if r != "--check"]
            assignments = parse_assignments(rest)
            if not assignments:
                print("Provide assignments like: STR=15 DEX=10 CON=15 INT=8 WIS=11 CHA=12")
                sys.exit(1)
            do_pointbuy_check(assignments)
        else:
            # Print the cost table as reference
            print("Point Buy — 27 points, scores 8–15\n")
            print("Cost table:")
            print("  Score:  " + "  ".join(f"{s:>2}" for s in range(8, 16)))
            print("  Cost:   " + "  ".join(f"{POINT_BUY_COST[s]:>2}" for s in range(8, 16)))
            print()
            print("Tell the DM your six scores (STR DEX CON INT WIS CHA)")
            print("and run with --check to validate:")
            print("  python3 ability-scores.py pointbuy --check STR=15 DEX=10 CON=15 INT=8 WIS=11 CHA=12")

    elif mode == "modifiers":
        assignments = parse_assignments(rest)
        if not assignments:
            print("Provide scores like: STR=15 DEX=10 CON=15 INT=8 WIS=11 CHA=12")
            sys.exit(1)
        do_modifiers(assignments)

    else:
        print(f"Unknown mode: {mode}. Use: roll | pointbuy | modifiers")
        sys.exit(1)
