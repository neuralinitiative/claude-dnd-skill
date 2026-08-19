"""
npc_rename.py — rename a character (NPC or PC) across an entire campaign.

Updates every file the name appears in, atomically, with backup-first safety:
  - npcs.md, npcs-full.md
  - state.md (every section — Current Situation, Live State Flags, Active
    Quests, Recent Events, Faction Moves, Continuity Archive, DM Notes)
  - session-log.md (current sessions)
  - characters/<slug>.md (renames file too if PC) + global roster mirror
  - graph.json (renames node by name; edges preserved — they reference IDs)

session-log-archive.md is left untouched by default (historical accuracy)
unless --include-archive is passed. A one-line audit note is added to the
archive top noting the rename.

Usage:
    python3 npc_rename.py --campaign C --old "Old Name" --new "New Name"
    python3 npc_rename.py --campaign C --old "Old Name" --random
    python3 npc_rename.py --campaign C --old "Old Name" --new "New" --dry-run
    python3 npc_rename.py --campaign C --old "Old Name" --new "New" --yes
    python3 npc_rename.py --campaign C --old "Old Name" --new "New" --type pc
    python3 npc_rename.py --campaign C --old "Old Name" --new "New" --include-archive

Flags:
    --dry-run         show hits without writing
    --yes             skip confirmation prompt
    --type            npc (default) | pc — pc rename also moves the
                      character file and updates the global roster
    --include-archive also rename in session-log-archive.md (default: skip
                      to preserve historical accuracy; an audit note is
                      added to the archive top either way)
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import random
import re
import shutil
import subprocess
import sys

from paths import find_campaign, characters_dir, _root
from name_registry import slug, all_taken_slugs, add as registry_add, retire as registry_retire

# Windows UTF-8 fix: default piped stdout is cp936/GBK, which mangles Chinese
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ── Embedded fantasy-name corpus ──────────────────────────────────────────
# 80 first × 60 last = 4800 unique combinations. Sourced from common public-
# domain fantasy name patterns; no copyrighted material.
_FIRST = [
    "Aelric", "Aldon", "Alric", "Anselm", "Arden", "Bael", "Branwen", "Caelum",
    "Caen", "Cassian", "Cedric", "Corran", "Corvin", "Daric", "Drest", "Eamon",
    "Elen", "Ember", "Erland", "Eryn", "Fenric", "Galen", "Gareth", "Gennar",
    "Hadrian", "Halric", "Hartwin", "Idris", "Iren", "Jovan", "Kael", "Kerith",
    "Lael", "Lirien", "Lorcan", "Lyra", "Maerin", "Mairwen", "Marrick", "Merrin",
    "Naren", "Nerys", "Niall", "Olen", "Oren", "Perrin", "Quinn", "Rael",
    "Renwick", "Rhydian", "Rilen", "Rorik", "Saern", "Selwyn", "Senna", "Sevren",
    "Sorin", "Talin", "Tamsyn", "Theron", "Tiernan", "Tordis", "Trevin", "Ulric",
    "Valen", "Vellin", "Verra", "Wendel", "Wren", "Xira", "Yael", "Yarrick",
    "Ysolde", "Zephir", "Auren", "Brel", "Cyrith", "Dorvel", "Fellan", "Hesta",
]

_LAST = [
    "Ashbourne", "Bellweather", "Blackmoor", "Brackenhold", "Brightspire", "Carrowfell",
    "Castermane", "Cinderhold", "Coldspring", "Crowmere", "Dalebrook", "Darkmoor",
    "Drystone", "Edgewater", "Embervale", "Fairwynd", "Fenlocke", "Fellscarp",
    "Frostgale", "Glasswain", "Greybarrow", "Hallowind", "Harrowmere", "Hartwell",
    "Hollowstride", "Innesglen", "Ironholt", "Karnwell", "Larkmere", "Lockmoor",
    "Marrowfen", "Mistwarden", "Nightbrook", "Oakhaven", "Pellsworth", "Quintain",
    "Ravenhall", "Reedstrand", "Rookwell", "Sablecroft", "Saltmere", "Silverbrook",
    "Stormcrest", "Tallowmere", "Thornwell", "Tindalbrook", "Underwich", "Vaelmoor",
    "Vexworth", "Whitethorn", "Wilderbrook", "Yarrowfield", "Zorrenglade", "Brackleaf",
    "Coalwarden", "Drosspike", "Greengrave", "Hexmere", "Lampwood", "Pinegate",
]


def random_name(taken_slugs: set[str]) -> str:
    """Pick first + last from corpus; reject if slug already taken; up to 50 retries.

    Falls back to a numerical suffix if the rare draw exhausts the search.
    """
    for _ in range(50):
        candidate = f"{random.choice(_FIRST)} {random.choice(_LAST)}"
        if slug(candidate) not in taken_slugs:
            return candidate
    # Fallback — suffix with random 3-digit number
    return f"{random.choice(_FIRST)} {random.choice(_LAST)}-{random.randint(100, 999)}"


# ── File-set discovery ────────────────────────────────────────────────────

_TEXT_FILES_ALWAYS = ["npcs.md", "npcs-full.md", "state.md",
                      "session-log.md", "world.md",
                      "session_tail.json"]  # display companion's last-N events
_TEXT_FILES_OPTIONAL = ["session-log-archive.md"]

# Honorific prefixes to strip when computing title-stripped variants.
# When renaming "Brother Calshen Drey" we ALSO want to match bare
# "Calshen Drey" references in the same files. Only strips when the
# remaining tail is ≥ 2 words and ≥ 6 characters (avoids "Brother John"
# stripping to ambiguous "John").
_HONORIFICS = {
    "brother", "sister", "father", "mother", "lord", "lady", "sir", "dame",
    "captain", "master", "mistress", "miss", "mr", "mr.", "mrs", "mrs.",
    "ms", "ms.", "dr", "dr.", "king", "queen", "prince", "princess",
    "duke", "duchess", "count", "countess", "baron", "baroness", "doctor",
    "professor", "elder", "abbot", "abbess",
}


def _title_stripped(name: str) -> str | None:
    """Return the name without leading honorific, or None if not eligible.

    Only returns a stripped form when:
      - first word is in _HONORIFICS
      - remaining tail has ≥ 2 words AND ≥ 6 characters total
    """
    parts = name.strip().split()
    if len(parts) < 3:
        return None
    if parts[0].lower().rstrip(".") not in _HONORIFICS:
        return None
    tail = " ".join(parts[1:])
    if len(tail) < 6:
        return None
    return tail


def _name_variants(name: str) -> list[str]:
    """Return all variants of a name to search/replace.

    First the full name, then the title-stripped form if eligible.
    Order matters — the full form must be replaced first so the stripped
    form doesn't accidentally swap inside the already-replaced text.
    """
    variants = [name]
    stripped = _title_stripped(name)
    if stripped:
        variants.append(stripped)
    return variants


def _files_to_scan(camp_dir: pathlib.Path,
                   include_archive: bool) -> list[pathlib.Path]:
    out = []
    for name in _TEXT_FILES_ALWAYS:
        f = camp_dir / name
        if f.exists():
            out.append(f)
    if include_archive:
        for name in _TEXT_FILES_OPTIONAL:
            f = camp_dir / name
            if f.exists():
                out.append(f)
    return out


# ── Hit detection ─────────────────────────────────────────────────────────

def _whole_word_pattern(name: str) -> re.Pattern:
    """Match the full name as a whole-word sequence; case-sensitive.

    Word-boundary aware so 'Sera' doesn't match inside 'Seraphim' but does
    match in 'Sera Voss' or 'Sera,'. Multi-word names are joined with a
    flexible whitespace pattern so 'Aldric  Voss' (double space) still matches.
    """
    parts = [re.escape(p) for p in name.strip().split()]
    body = r"\s+".join(parts)
    return re.compile(rf"\b{body}\b")


def find_hits(camp_dir: pathlib.Path, old: str,
              include_archive: bool) -> dict[pathlib.Path, list[tuple[int, str]]]:
    """Scan all relevant files; return {file: [(line_num, line), ...]}.

    Searches both the full name AND the title-stripped variant (e.g.
    'Calshen Drey' for 'Brother Calshen Drey'). Each hit line is reported
    once even if both variants match it.
    """
    variants = _name_variants(old)
    patterns = [_whole_word_pattern(v) for v in variants]
    hits: dict[pathlib.Path, list[tuple[int, str]]] = {}
    for f in _files_to_scan(camp_dir, include_archive):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        matches: list[tuple[int, str]] = []
        seen_lines: set[int] = set()
        for n, line in enumerate(text.splitlines(), start=1):
            if any(p.search(line) for p in patterns) and n not in seen_lines:
                matches.append((n, line.rstrip()))
                seen_lines.add(n)
        if matches:
            hits[f] = matches
    return hits


# ── Apply ─────────────────────────────────────────────────────────────────

def apply_text_rename(path: pathlib.Path, old: str, new: str) -> int:
    """Replace each variant of `old` with the corresponding variant of `new`.

    Variant pairing:
      - full(old) → full(new)
      - stripped(old) → stripped(new) if stripped(new) exists, else full(new)

    Order: full first (longest match wins), then stripped — so we don't
    accidentally double-replace inside an already-substituted string.
    Returns total replacement count across all variants.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    total = 0

    old_full = old
    new_full = new
    pat_full = _whole_word_pattern(old_full)
    text, n = pat_full.subn(new_full, text)
    total += n

    old_stripped = _title_stripped(old)
    if old_stripped:
        new_stripped = _title_stripped(new) or new_full
        pat_stripped = _whole_word_pattern(old_stripped)
        text, n = pat_stripped.subn(new_stripped, text)
        total += n

    if total > 0:
        path.write_text(text, encoding="utf-8")
    return total


def apply_graph_rename(graph_path: pathlib.Path, old: str, new: str) -> bool:
    """Rename node whose .name == old → new. Edges reference node IDs so
    they're untouched. Also rewrites the slug-prefixed node id if present.

    Returns True if a node was renamed.
    """
    if not graph_path.exists():
        return False
    try:
        data = json.loads(graph_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    old_slug = slug(old)
    new_slug = slug(new)
    renamed = False
    nodes = data.get("nodes", [])
    id_remap: dict[str, str] = {}
    for node in nodes:
        if node.get("name") == old:
            node["name"] = new
            # Remap node id if it follows the type_slug convention
            old_id = node.get("id", "")
            ntype = node.get("type", "npc")
            expected_old_id = f"{ntype}_{old_slug}"
            if old_id == expected_old_id:
                new_id = f"{ntype}_{new_slug}"
                node["id"] = new_id
                id_remap[old_id] = new_id
            renamed = True
    # Rewrite edges that reference renamed node ids
    for edge in data.get("edges", []):
        for k in ("from", "to"):
            if edge.get(k) in id_remap:
                edge[k] = id_remap[edge[k]]
    if renamed:
        graph_path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    return renamed


def add_archive_audit_note(camp_dir: pathlib.Path, old: str, new: str,
                           session: int) -> bool:
    """Add a one-line audit note at the top of session-log-archive.md."""
    archive = camp_dir / "session-log-archive.md"
    if not archive.exists():
        return False
    text = archive.read_text(encoding="utf-8", errors="replace")
    today = datetime.date.today().isoformat()
    note = (f"<!-- rename audit {today}: '{old}' renamed to '{new}' "
            f"at S{session}; historical entries below preserve the original name -->\n")
    if note.split(": ", 1)[1].split(" at S")[0] in text:
        # Already noted — don't double-note
        return False
    archive.write_text(note + text, encoding="utf-8")
    return True


# ── Main flow ─────────────────────────────────────────────────────────────

def _read_session_count(camp_dir: pathlib.Path) -> int:
    state = camp_dir / "state.md"
    if not state.exists():
        return 0
    m = re.search(r"\*\*Session count:\*\*\s*(\d+)",
                  state.read_text(encoding="utf-8", errors="replace"))
    return int(m.group(1)) if m else 0


def _backup(camp_dir: pathlib.Path) -> pathlib.Path:
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = camp_dir.with_name(f"{camp_dir.name}.backup-rename-{ts}")
    shutil.copytree(camp_dir, target)
    return target


def _confirm(prompt: str) -> bool:
    try:
        ans = input(prompt + " [y/n] ").strip().lower()
    except EOFError:
        return False
    return ans in {"y", "yes"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--campaign", required=True)
    p.add_argument("--old", required=True, help="existing character name")
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--new", help="explicit new name")
    grp.add_argument("--random", action="store_true",
                     help="pick a registry-safe random name")
    p.add_argument("--type", choices=["npc", "pc"], default="npc")
    p.add_argument("--dry-run", action="store_true",
                   help="show hits without writing")
    p.add_argument("--yes", action="store_true",
                   help="skip confirmation prompt")
    p.add_argument("--include-archive", action="store_true",
                   help="also rename in session-log-archive.md "
                        "(default: leave historical text intact)")

    args = p.parse_args()

    camp_dir = find_campaign(args.campaign)
    if not camp_dir.exists():
        print(f"npc_rename: campaign '{args.campaign}' not found at {camp_dir}",
              file=sys.stderr)
        return 1

    # Resolve --new (explicit or random)
    if args.random:
        taken = all_taken_slugs()
        new_name = random_name(taken)
        print(f"  --random picked: {new_name}")
    else:
        new_name = args.new

    # Find hits
    hits = find_hits(camp_dir, args.old, args.include_archive)
    if not hits:
        print(f"npc_rename: no occurrences of '{args.old}' found in {camp_dir.name}",
              file=sys.stderr)
        return 1

    total_lines = sum(len(v) for v in hits.values())
    print(f"\n=== rename plan: '{args.old}' → '{new_name}' in {camp_dir.name} ===")
    print(f"  files affected : {len(hits)}")
    print(f"  total matches  : {total_lines}\n")
    for f, matches in hits.items():
        print(f"  {f.name}: {len(matches)} matches")
        for ln, line in matches[:3]:
            preview = line if len(line) <= 120 else line[:117] + "..."
            print(f"    L{ln}: {preview}")
        if len(matches) > 3:
            print(f"    ... and {len(matches) - 3} more")
    # Graph rename preview
    g = camp_dir / "graph.json"
    if g.exists():
        try:
            data = json.loads(g.read_text(encoding="utf-8"))
            graph_match = any(n.get("name") == args.old for n in data.get("nodes", []))
            if graph_match:
                print(f"  graph.json: 1 node will be renamed (edges preserved)")
        except json.JSONDecodeError:
            pass
    # PC file rename preview
    if args.type == "pc":
        old_pc = camp_dir / "characters" / f"{slug(args.old)}.md"
        if old_pc.exists():
            print(f"  characters/{slug(args.old)}.md → characters/{slug(new_name)}.md")
            print(f"  WARNING: PC rename also updates global roster at "
                  f"{characters_dir() / (slug(args.old) + '.md')}")

    if args.dry_run:
        print("\n  --dry-run set; no writes performed.")
        return 0

    if not args.yes:
        if not _confirm("\nApply rename (with backup)?"):
            print("  cancelled.")
            return 1

    # Backup
    backup = _backup(camp_dir)
    print(f"\n  backup: {backup}")

    # Apply text renames
    total_replacements = 0
    for f in list(hits.keys()):
        n = apply_text_rename(f, args.old, new_name)
        print(f"    {f.name}: {n} replacements")
        total_replacements += n

    # Apply graph rename
    if g.exists():
        if apply_graph_rename(g, args.old, new_name):
            print(f"    graph.json: node renamed")

    # PC file rename
    if args.type == "pc":
        old_pc = camp_dir / "characters" / f"{slug(args.old)}.md"
        if old_pc.exists():
            new_pc = camp_dir / "characters" / f"{slug(new_name)}.md"
            old_pc.rename(new_pc)
            print(f"    characters/{slug(args.old)}.md → characters/{slug(new_name)}.md")
            # Mirror to global roster
            global_old = characters_dir() / f"{slug(args.old)}.md"
            global_new = characters_dir() / f"{slug(new_name)}.md"
            if global_old.exists():
                shutil.copy(new_pc, global_new)
                global_old.unlink()
                print(f"    global roster updated")

    # Archive audit note (only if NOT including archive in rename)
    if not args.include_archive:
        sess = _read_session_count(camp_dir)
        if add_archive_audit_note(camp_dir, args.old, new_name, sess):
            print(f"    session-log-archive.md: audit note added at top")

    # Update name registry
    sess = _read_session_count(camp_dir)
    registry_retire(args.old, args.campaign, replaced_by=new_name)
    registry_add(new_name, args.type, args.campaign, sess)
    print(f"    name_registry: '{args.old}' retired, '{new_name}' added")

    print(f"\n  done. {total_replacements} text replacements + graph + registry updates.")
    print(f"  To revert: rm -rf '{camp_dir}' && mv '{backup}' '{camp_dir}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
