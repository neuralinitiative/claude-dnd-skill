"""
session_recap.py — deterministic state-diff between two character snapshots.

Ported from the Neural Initiative engine's recap module, re-implemented in the
skill's idiom (plain Python + JSON + markdown character sheets, no Pydantic).

Why this exists (zero LLM):
  - Recaps are the #1 thing an LLM hallucinates — wrong HP, dropped facts,
    invented events. State diffs are pure data; the model should never compute
    them.
  - Replacing "tell me what happened to the party last session" with a
    precomputed one-paragraph summary saves a few hundred tokens at every
    `/dm:dnd load`.

What it snapshots, per character:
  HP (current/max/temp), level, hit dice remaining, death saves,
  conditions, concentration, exhaustion, inspiration, spell slots expended.

Sources:
  - character sheet markdown at `<campaign>/characters/<name>.md` (canonical
    persisted state — HP, level, slots, hit dice, death saves)
  - the campaign's `tracker.json` (live runtime state — conditions,
    concentration) is merged in when present, so a snapshot reflects the
    in-encounter picture too.

CLI:
  python3 session_recap.py snapshot --campaign NAME [--out FILE]
      Snapshot the party now. Default out: <campaign>/.recap/snapshot-<ts>.json
      and also updates <campaign>/.recap/last.json.

  python3 session_recap.py diff --campaign NAME [--before FILE] [--after FILE]
      Diff two snapshots into a plain-English summary. With no --before, uses
      the previous snapshot (<campaign>/.recap/prev.json); with no --after,
      snapshots the current state on the fly. Prints a summary line suitable to
      inject at session load.

  python3 session_recap.py diff-files A.json B.json
      Diff two snapshot files directly (no campaign needed).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

try:
    from paths import find_campaign
except ImportError:  # pragma: no cover - allow standalone import
    find_campaign = None  # type: ignore


# 5e SRD conditions, for normalizing condition tokens parsed from sheets.
_SRD_CONDITIONS = {
    "blinded", "charmed", "deafened", "exhausted", "frightened", "grappled",
    "incapacitated", "invisible", "paralyzed", "petrified", "poisoned",
    "prone", "restrained", "stunned", "unconscious",
}


# ── character-sheet parsing ──────────────────────────────────────────────────


def _first(pattern: str, text: str, flags: int = re.IGNORECASE):
    m = re.search(pattern, text, flags)
    return m if m else None


def parse_character_sheet(text: str) -> dict:
    """Parse a character-sheet markdown file into a snapshot dict.

    Tolerant of partially-filled sheets — missing fields default sensibly so a
    half-finished sheet still diffs cleanly. Returns:
      {name, level, current_hp, max_hp, temp_hp, hit_dice_remaining,
       death_saves: {successes, failures}, exhaustion, inspiration,
       concentration, conditions: [], spell_slots_expended: {level: n}}
    """
    snap: dict = {
        "name": "",
        "level": None,
        "current_hp": None,
        "max_hp": None,
        "temp_hp": 0,
        "hit_dice_remaining": {},
        "death_saves": {"successes": 0, "failures": 0},
        "exhaustion": 0,
        "inspiration": False,
        "concentration": None,
        "conditions": [],
        "spell_slots_expended": {},
    }

    # Name: first H1.
    m = _first(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    if m:
        snap["name"] = m.group(1).strip().strip("<>")

    # Level: "**Level:** 3" anywhere (the Identity line packs several fields).
    m = _first(r"\*\*Level:\*\*\s*([0-9]+)", text)
    if m:
        snap["level"] = int(m.group(1))

    # HP: "**HP:** 18 / 30" — current / max.
    m = _first(r"\*\*HP:\*\*\s*([0-9]+)\s*/\s*([0-9]+)", text)
    if m:
        snap["current_hp"] = int(m.group(1))
        snap["max_hp"] = int(m.group(2))

    # Temp HP: "**Temp HP:** 5"
    m = _first(r"\*\*Temp HP:\*\*\s*([0-9]+)", text)
    if m:
        snap["temp_hp"] = int(m.group(1))

    # Hit Dice: "**Hit Dice:** 3d8 (remaining: 2)"
    m = _first(r"\*\*Hit Dice:\*\*\s*([0-9]+)d([0-9]+)\s*\(remaining:\s*([0-9]+)\)",
               text)
    if m:
        die = f"d{m.group(2)}"
        snap["hit_dice_remaining"][die] = int(m.group(3))

    # Death Saves: "**Death Saves:** Successes: 1 | Failures: 2"
    m = _first(r"Successes:\s*([0-9]+)\s*\|\s*Failures:\s*([0-9]+)", text)
    if m:
        snap["death_saves"] = {
            "successes": int(m.group(1)),
            "failures": int(m.group(2)),
        }

    # Exhaustion: "**Exhaustion:** 2" (optional field).
    m = _first(r"\*\*Exhaustion:\*\*\s*([0-9]+)", text)
    if m:
        snap["exhaustion"] = int(m.group(1))

    # Inspiration: "**Inspiration:** yes/no/1/0" (optional field).
    m = _first(r"\*\*Inspiration:\*\*\s*(yes|no|true|false|1|0|✓|—)", text)
    if m:
        snap["inspiration"] = m.group(1).lower() in {"yes", "true", "1", "✓"}

    # Conditions: "**Conditions:** poisoned, prone" (optional inline field).
    m = _first(r"\*\*Conditions:\*\*\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    if m:
        snap["conditions"] = _parse_conditions(m.group(1))

    # Concentration: "**Concentration:** Bless" (optional field).
    m = _first(r"\*\*Concentration:\*\*\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    if m:
        val = m.group(1).strip()
        if val and val.lower() not in {"none", "—", "-", "n/a"}:
            snap["concentration"] = val

    # Spell slots: the "## Spell Slots" table — Level | Total | Used rows.
    snap["spell_slots_expended"].update(_parse_spell_slots(text))

    return snap


_LEVEL_TOKENS = {
    "1st": "1", "2nd": "2", "3rd": "3", "4th": "4", "5th": "5",
    "6th": "6", "7th": "7", "8th": "8", "9th": "9",
}


def _parse_spell_slots(text: str) -> dict:
    """Parse the Spell Slots table into {slot_level_str: used_count}.

    Only records levels with a non-zero, numeric Used cell.
    """
    out: dict = {}
    section = _first(r"##\s*Spell Slots.*?(?=\n##\s|\Z)", text,
                     re.IGNORECASE | re.DOTALL)
    if not section:
        return out
    body = section.group(0)
    for row in re.finditer(r"^\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|",
                           body, re.MULTILINE):
        lvl_raw = row.group(1).strip().lower()
        if lvl_raw in {"level", "---", ""} or set(lvl_raw) <= {"-", " "}:
            continue
        used_raw = row.group(3).strip()
        lvl = _LEVEL_TOKENS.get(lvl_raw, lvl_raw.rstrip("stndrh"))
        try:
            used = int(used_raw)
        except ValueError:
            continue
        if used:
            out[lvl] = used
    return out


def _parse_conditions(raw: str) -> list:
    """Split a comma/space list of conditions and normalize to lowercase."""
    tokens = re.split(r"[,/]| and ", raw)
    out = []
    for tok in tokens:
        c = tok.strip().lower().strip(".")
        if not c or c in {"none", "—", "-", "n/a"}:
            continue
        out.append(c)
    return out


# ── tracker.json merge (live runtime state) ──────────────────────────────────


def _merge_tracker(snap: dict, tracker: dict) -> None:
    """Overlay live conditions/concentration/death saves from tracker.json
    onto a sheet-derived snapshot, matched by lowercased name."""
    if not snap.get("name"):
        return
    key = snap["name"].lower()
    entry = tracker.get(key)
    if not entry:
        return
    conds = entry.get("conditions")
    if conds:
        merged = set(snap.get("conditions") or []) | {c.lower() for c in conds}
        snap["conditions"] = sorted(merged)
    if entry.get("concentration"):
        snap["concentration"] = entry["concentration"]
    ds = entry.get("death_saves")
    if ds:
        snap["death_saves"] = {
            "successes": ds.get("successes", 0),
            "failures": ds.get("failures", 0),
        }


# ── party snapshot ───────────────────────────────────────────────────────────


def snapshot_party(campaign_dir: pathlib.Path) -> dict:
    """Build a full-party snapshot from <campaign>/characters/*.md, merging any
    live tracker.json state. Returns {"taken": ts, "characters": {name: snap}}.
    """
    chars: dict = {}
    char_dir = campaign_dir / "characters"
    if char_dir.is_dir():
        for sheet in sorted(char_dir.glob("*.md")):
            snap = parse_character_sheet(sheet.read_text(encoding="utf-8"))
            if not snap.get("name"):
                snap["name"] = sheet.stem
            chars[snap["name"]] = snap

    tracker_path = campaign_dir / "tracker.json"
    if tracker_path.exists():
        try:
            tracker = json.loads(tracker_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            tracker = {}
        for snap in chars.values():
            _merge_tracker(snap, tracker)

    return {"taken": int(time.time()), "characters": chars}


# ── diffing ──────────────────────────────────────────────────────────────────


def diff_character(before: dict, after: dict) -> list:
    """Compute ordered list of change dicts between two character snapshots.

    Each change: {"character", "kind", "before", "after", "detail"}.
    """
    name = after.get("name") or before.get("name") or "?"
    out: list = []

    def add(kind, b, a, detail=""):
        out.append({"character": name, "kind": kind, "before": b,
                    "after": a, "detail": detail})

    if before.get("current_hp") != after.get("current_hp"):
        add("hp", before.get("current_hp"), after.get("current_hp"))
    if before.get("max_hp") != after.get("max_hp"):
        add("max_hp", before.get("max_hp"), after.get("max_hp"))
    if (before.get("temp_hp") or 0) != (after.get("temp_hp") or 0):
        add("temp_hp", before.get("temp_hp") or 0, after.get("temp_hp") or 0)
    if before.get("level") != after.get("level"):
        add("level", before.get("level"), after.get("level"))
    if (before.get("exhaustion") or 0) != (after.get("exhaustion") or 0):
        add("exhaustion", before.get("exhaustion") or 0,
            after.get("exhaustion") or 0)
    if bool(before.get("inspiration")) != bool(after.get("inspiration")):
        add("inspiration", bool(before.get("inspiration")),
            bool(after.get("inspiration")))
    if before.get("concentration") != after.get("concentration"):
        add("concentration", before.get("concentration"),
            after.get("concentration"))

    before_conds = set(before.get("conditions") or [])
    after_conds = set(after.get("conditions") or [])
    for c in sorted(after_conds - before_conds):
        add("condition_added", None, c)
    for c in sorted(before_conds - after_conds):
        add("condition_removed", c, None)

    bslots = before.get("spell_slots_expended") or {}
    aslots = after.get("spell_slots_expended") or {}
    for lvl in sorted(set(bslots) | set(aslots), key=lambda x: str(x)):
        b = bslots.get(lvl, 0)
        a = aslots.get(lvl, 0)
        if b != a:
            add("spell_slot", b, a, detail=f"level {lvl}")

    bhd = before.get("hit_dice_remaining") or {}
    ahd = after.get("hit_dice_remaining") or {}
    for die in sorted(set(bhd) | set(ahd)):
        b = bhd.get(die, 0)
        a = ahd.get(die, 0)
        if b != a:
            add("hit_die", b, a, detail=die)

    bds = before.get("death_saves") or {}
    ads = after.get("death_saves") or {}
    if bds.get("successes", 0) != ads.get("successes", 0):
        add("death_save_success", bds.get("successes", 0),
            ads.get("successes", 0))
    if bds.get("failures", 0) != ads.get("failures", 0):
        add("death_save_failure", bds.get("failures", 0),
            ads.get("failures", 0))

    return out


def diff_party(before: dict, after: dict) -> list:
    """Diff two party snapshots (matched by character name). Characters present
    on only one side yield no diff — joins/leaves are tracked elsewhere."""
    bchars = before.get("characters", {})
    achars = after.get("characters", {})
    changes: list = []
    for name in sorted(bchars.keys() & achars.keys()):
        changes.extend(diff_character(bchars[name], achars[name]))
    return changes


# ── plain-English rendering ──────────────────────────────────────────────────


def _phrase(c: dict) -> str:
    kind = c["kind"]
    b, a, detail = c["before"], c["after"], c.get("detail", "")
    if kind == "hp":
        if b is None or a is None:
            return f"HP now {a}"
        delta = int(a) - int(b)
        if delta < 0:
            return f"took {abs(delta)} damage ({b}→{a} HP)"
        if delta > 0:
            return f"healed {delta} HP ({b}→{a})"
    if kind == "max_hp":
        return f"max HP {b}→{a}"
    if kind == "temp_hp":
        return f"temp HP {b}→{a}"
    if kind == "level":
        return f"levelled up to {a}"
    if kind == "exhaustion":
        delta = int(a) - int(b)
        if delta > 0:
            return f"gained {delta} exhaustion (now {a})"
        return f"recovered exhaustion (now {a})"
    if kind == "inspiration":
        return "gained Inspiration" if a else "spent Inspiration"
    if kind == "concentration":
        if a:
            return f"concentrating on {a}"
        return f"dropped concentration on {b}"
    if kind == "condition_added":
        return f"gained {a.capitalize()}"
    if kind == "condition_removed":
        return f"no longer {b.capitalize()}"
    if kind == "spell_slot":
        delta = int(a) - int(b)
        if delta > 0:
            return f"spent {delta} {detail} slot{'s' if delta != 1 else ''}"
        return f"recovered {abs(delta)} {detail} slot{'s' if abs(delta) != 1 else ''}"
    if kind == "hit_die":
        delta = int(a) - int(b)
        if delta < 0:
            return f"spent {abs(delta)} {detail} hit die"
        return f"recovered {delta} {detail} hit die"
    if kind == "death_save_success":
        return f"death-save successes {b}→{a}"
    if kind == "death_save_failure":
        return f"death-save failures {b}→{a}"
    return ""


def render_summary(changes: list) -> str:
    """One-paragraph plain-English summary, grouped by character. Empty string
    if nothing changed."""
    if not changes:
        return ""
    by_char: dict = {}
    for c in changes:
        by_char.setdefault(c["character"], []).append(c)
    lines = []
    for char, ch_list in by_char.items():
        phrases = [p for p in (_phrase(c) for c in ch_list) if p]
        if phrases:
            lines.append(f"{char}: {'; '.join(phrases)}.")
    return " ".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────


def _recap_dir(campaign_dir: pathlib.Path) -> pathlib.Path:
    d = campaign_dir / ".recap"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve_campaign(name: str) -> pathlib.Path:
    if find_campaign is None:
        raise RuntimeError("paths.find_campaign unavailable; run from skill scripts dir")
    return find_campaign(name)


def cmd_snapshot(args) -> int:
    campaign_dir = _resolve_campaign(args.campaign)
    snap = snapshot_party(campaign_dir)
    out_text = json.dumps(snap, indent=2, ensure_ascii=False)

    rdir = _recap_dir(campaign_dir)
    # Roll last → prev so a later `diff` can compare against the prior snapshot.
    last = rdir / "last.json"
    if last.exists():
        (rdir / "prev.json").write_text(last.read_text(encoding="utf-8"),
                                        encoding="utf-8")
    last.write_text(out_text, encoding="utf-8")
    ts_path = rdir / f"snapshot-{snap['taken']}.json"
    ts_path.write_text(out_text, encoding="utf-8")

    out = args.out
    if out:
        pathlib.Path(out).expanduser().write_text(out_text, encoding="utf-8")
    print(f"# snapshot of {len(snap['characters'])} characters "
          f"→ {ts_path}", file=sys.stderr)
    if args.print:
        print(out_text)
    return 0


def cmd_diff(args) -> int:
    campaign_dir = _resolve_campaign(args.campaign)
    rdir = _recap_dir(campaign_dir)

    last = rdir / "last.json"

    if args.before:
        before = json.loads(pathlib.Path(args.before).expanduser().read_text())
    else:
        if not last.exists():
            print("# no prior snapshot to diff against; run `snapshot` first",
                  file=sys.stderr)
            return 0
        before = json.loads(last.read_text(encoding="utf-8"))

    live = not args.after
    if args.after:
        after = json.loads(pathlib.Path(args.after).expanduser().read_text())
    else:
        after = snapshot_party(campaign_dir)

    changes = diff_party(before, after)
    summary = render_summary(changes)
    if args.json:
        print(json.dumps({"changes": changes, "summary": summary},
                         indent=2, ensure_ascii=False))
    else:
        print(summary if summary else "# no state changes since last snapshot")

    # Advance the baseline so consecutive `diff` calls chain turn-to-turn:
    # the live snapshot we just compared becomes the next diff's "before".
    # Skipped for ad-hoc diffs (--before/--after overrides) and under --no-roll.
    if live and not args.before and not getattr(args, "no_roll", False):
        if last.exists():
            (rdir / "prev.json").write_text(last.read_text(encoding="utf-8"),
                                            encoding="utf-8")
        last.write_text(json.dumps(after, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    return 0


def cmd_diff_files(args) -> int:
    before = json.loads(pathlib.Path(args.before).expanduser().read_text())
    after = json.loads(pathlib.Path(args.after).expanduser().read_text())
    changes = diff_party(before, after)
    summary = render_summary(changes)
    if args.json:
        print(json.dumps({"changes": changes, "summary": summary},
                         indent=2, ensure_ascii=False))
    else:
        print(summary if summary else "# no state changes")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="session_recap", description=__doc__.split("\n", 2)[1])
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("snapshot", help="snapshot the party's current state")
    sp.add_argument("--campaign", required=True)
    sp.add_argument("--out", help="also write the snapshot JSON to this path")
    sp.add_argument("--print", action="store_true", help="print snapshot to stdout")
    sp.set_defaults(func=cmd_snapshot)

    sp = sub.add_parser("diff", help="diff against the last snapshot and print a summary")
    sp.add_argument("--campaign", required=True)
    sp.add_argument("--before", help="explicit before-snapshot file (default: prev/last)")
    sp.add_argument("--after", help="explicit after-snapshot file (default: snapshot now)")
    sp.add_argument("--json", action="store_true", help="emit structured changes + summary")
    sp.add_argument("--no-roll", action="store_true",
                    help="don't advance the baseline (compare again next time against the same snapshot)")
    sp.set_defaults(func=cmd_diff)

    sp = sub.add_parser("diff-files", help="diff two snapshot files directly")
    sp.add_argument("before")
    sp.add_argument("after")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_diff_files)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
