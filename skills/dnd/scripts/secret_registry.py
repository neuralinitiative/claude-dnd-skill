"""
secret_registry.py — scene brief from the knowledge registry (campaigns/<name>/secrets.md).

The DM knows every secret; the world doesn't. This prints, for the characters
present in a scene, every registered secret that at least one of them does NOT
know, with the cover story they perceive instead. Run it at /dm:dnd load and
whenever the cast of a scene changes, so the cover is in front of the DM at the
moment an NPC speaks.

Usage:
  python3 secret_registry.py brief --campaign <name> --present "Orrin Vale,the innkeeper"
  python3 secret_registry.py brief --campaign <name> --all     # every active entry
  python3 secret_registry.py list  --campaign <name>
  (--file PATH instead of --campaign reads a registry directly)

secrets.md format (field labels stay in English; content in the table's language):

  ## Secrets
  ### <entry-id> — <label>
  - **Kind:** pc | party | quest | npc | faction | world
  - **Truth:** ...
  - **Cover:** what those who don't know perceive instead
  - **Known by:** Name (S3, saw) · Name (S5, told by X)
  - **Suspected by:** Name (S6, why) — or *none*
  - **Must not learn it:** Name — or *none*
  - **If revealed:** ...

  ## Revealed / public      <- entries here are common knowledge and skipped

Names match loosely: case, accents and punctuation are ignored, and a shorter
name matches a longer one when all its words appear in it ("Orrin" matches
"Orrin Vale").
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from utf8io import read_text, TextDecodeError  # noqa: E402

FIELDS = {
    "kind": "kind",
    "truth": "truth",
    "cover": "cover",
    "known by": "known",
    "suspected by": "suspected",
    "must not learn it": "forbidden",
    "if revealed": "if_revealed",
    "notes": "notes",
}
_FIELD_RE = re.compile(r"^\s*[-*]\s*\*\*(?P<label>[^*:]+):?\*\*:?\s*(?P<value>.*)$")
_ENTRY_SEP_RE = re.compile(r"\s+[—–-]\s+")
_NONE_RE = re.compile(r"^[\s*_]*(none|nobody|aucun|aucune|personne|—|-)?[\s*_.]*$", re.I)


def _norm_words(name: str) -> frozenset:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return frozenset(w for w in re.split(r"[^\w]+", s) if w)


def names_match(a: str, b: str) -> bool:
    wa, wb = _norm_words(a), _norm_words(b)
    if not wa or not wb:
        return False
    return wa <= wb or wb <= wa


def split_people(value: str) -> list[tuple[str, str]]:
    """'A (S3, saw) · B; C' -> [('A', '(S3, saw)'), ('B', ''), ('C', '')].
    Separators (· ; ,) inside parentheses are kept with their item."""
    if _NONE_RE.match(value or ""):
        return []
    items, depth, cur = [], 0, []
    for ch in value:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if depth == 0 and ch in "·;,":
            items.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    items.append("".join(cur))
    out = []
    for raw in items:
        raw = raw.strip().strip("*_").strip()
        if not raw or _NONE_RE.match(raw):
            continue
        name, _, rest = raw.partition("(")
        name = re.split(r"\s+[—–]\s+", name)[0].strip().strip("*_").strip()
        detail = ("(" + rest).strip() if rest else ""
        if name:
            out.append((name, detail))
    return out


def parse(text: str) -> list[dict]:
    """Active entries (not under a 'Revealed' heading), in file order."""
    entries, cur, revealed = [], None, False
    for line in text.splitlines():
        if line.startswith("## "):
            revealed = line[3:].strip().casefold().startswith("revealed")
            cur = None
            continue
        if line.startswith("### "):
            cur = None
            parts = _ENTRY_SEP_RE.split(line[4:].strip(), maxsplit=1)
            ident = parts[0].strip()
            label = parts[1] if len(parts) > 1 else ""
            if revealed or not ident or ident.startswith("<"):
                continue  # template placeholder or public entry
            cur = {"id": ident, "label": label.strip()}
            entries.append(cur)
            continue
        if cur is None:
            continue
        f = _FIELD_RE.match(line)
        if f:
            key = FIELDS.get(f.group("label").strip().casefold())
            if key:
                cur[key] = f.group("value").strip()
    for e in entries:
        e["known_people"] = split_people(e.get("known", ""))
        e["suspected_people"] = split_people(e.get("suspected", ""))
        e["forbidden_people"] = split_people(e.get("forbidden", ""))
    return entries


def _find(person: str, people: list[tuple[str, str]]):
    for name, detail in people:
        if names_match(person, name):
            return name, detail
    return None


def brief(entries: list[dict], present: list[str], show_all: bool = False) -> str:
    lines = []
    for e in entries:
        unaware, suspects, forbidden = [], [], []
        for p in present:
            if _find(p, e["known_people"]):
                continue
            s = _find(p, e["suspected_people"])
            if s:
                suspects.append(f"{p} {s[1]}".strip())
            else:
                unaware.append(p)
            if _find(p, e["forbidden_people"]):
                forbidden.append(p)
        if not (unaware or suspects or show_all):
            continue
        head = f"{e['id']}" + (f" — {e['label']}" if e["label"] else "")
        if e.get("kind"):
            head += f" [{e['kind']}]"
        lines.append(head)
        cover = e.get("cover", "")
        if cover and not _NONE_RE.match(cover):
            lines.append(f"  COVER (what they perceive): {cover}")
        if unaware:
            lines.append(f"  Does NOT know: {', '.join(unaware)}")
        if suspects:
            lines.append(f"  Only suspects (hints, never a flat statement): {'; '.join(suspects)}")
        if forbidden:
            lines.append(f"  MUST NOT LEARN IT: {', '.join(forbidden)}"
                         + (f" — if revealed: {e['if_revealed']}" if e.get("if_revealed") else ""))
        if show_all and not present:
            known = ", ".join(n for n, _ in e["known_people"]) or "nobody"
            lines.append(f"  Known by: {known}")
    if not lines:
        who = ", ".join(present) if present else "nobody"
        return f"# Knowledge brief — present: {who}\nNo registered secret is hidden from anyone present."
    header = f"# Knowledge brief — present: {', '.join(present)}" if present else "# Knowledge registry — all active entries"
    return header + "\n" + "\n".join(lines)


def _registry_path(args) -> pathlib.Path:
    if args.file:
        return pathlib.Path(args.file)
    from paths import find_campaign
    return find_campaign(args.campaign) / "secrets.md"


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Scene brief from the knowledge registry.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("brief", "list"):
        sp = sub.add_parser(name)
        src = sp.add_mutually_exclusive_group(required=True)
        src.add_argument("--campaign")
        src.add_argument("--file")
        if name == "brief":
            sp.add_argument("--present", default="",
                            help="comma-separated names of the characters present (NPCs and companions)")
            sp.add_argument("--all", action="store_true", help="show every active entry")
    args = ap.parse_args(argv)

    path = _registry_path(args)
    if not path.exists():
        print(f"# no knowledge registry at {path} — nothing to brief.")
        return 0
    try:
        entries = parse(read_text(path))
    except TextDecodeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.cmd == "list":
        for e in entries:
            print(f"{e['id']} — {e['label']} [{e.get('kind', '?')}] — known by {len(e['known_people'])}")
        if not entries:
            print("(no active entries)")
        return 0

    present = [p.strip() for p in args.present.split(",") if p.strip()]
    if not present and not args.all:
        print("error: brief needs --present NAMES or --all", file=sys.stderr)
        return 2
    print(brief(entries, present, show_all=args.all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
