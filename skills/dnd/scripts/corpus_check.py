#!/usr/bin/env python3
"""
corpus_check.py — validate the lazy-corpus layout of a structured (imported) campaign.

A structured campaign created by `/dm:dnd import` keeps the full source as a
lazily-loaded reference layer:

  <campaign>/
    arc.md              # full act/chapter tree (chapter ids like "1.1")
    source-index.md     # chapter-id -> source file -> one-line scope
    source/<id>.md      # one file per chapter, holding that chapter's source text

This linter checks that the three stay consistent:
  - every chapter id referenced in source-index.md has a source/<id>.md file
  - every source/<id>.md file is referenced by source-index.md (no orphans)
  - arc.md exists when a source layer is present
  - (advisory) flags any chapter source file large enough that reading it on
    demand would weigh on the per-load context — the lazy corpus reads a whole
    chapter at a time, so a giant chapter is the one way a structured campaign
    can still bloat a session; splitting it keeps play "a handful of chapters,
    not a whole book"

It is advisory: run it at the end of an import and during tests. Exit 0 = clean,
exit 1 = problems found (printed to stdout). A campaign with no source/ layer
(dynamic, sandbox, or an older import) is reported as "not a lazy-corpus
campaign" and exits 0 — there is nothing to validate.

Usage:
  python3 corpus_check.py --campaign <name>
"""

import sys
import os
import re
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

# Matches a source file reference like `source/1.1.md` anywhere in source-index.md.
_REF = re.compile(r"source/([A-Za-z0-9][\w.\-]*)\.md")

# A source/<id>.md past this many words weighs on the per-load context when the
# DM reads that whole chapter on demand. Advisory only — splitting the chapter
# into sub-chapters keeps a load light. ~12k words is roughly a fifth of the
# comfortable working budget on its own, which is where a single chapter starts
# to crowd everything else out.
LARGE_CHAPTER_WORDS = 12000


def indexed_ids(index_text: str) -> set:
    return set(_REF.findall(index_text))


def size_warnings(campaign: str) -> list:
    """Advisory: flag chapter source files large enough to bloat context when a
    single chapter loads. Layout-independent (a big chapter is a clean layout),
    so this is separate from check() and never changes the exit code."""
    camp = paths.find_campaign(campaign)
    source_dir = camp / "source"
    if not source_dir.exists():
        return []
    warns = []
    for p in sorted(source_dir.glob("*.md")):
        words = len(p.read_text(encoding="utf-8", errors="replace").split())
        if words > LARGE_CHAPTER_WORDS:
            warns.append(
                f"WARNING: source/{p.stem}.md is ~{words:,} words — large enough to weigh on "
                f"context when this chapter loads. Consider splitting it into sub-chapters "
                f"(e.g. {p.stem}a / {p.stem}b) across arc.md + source-index.md so play reads a "
                f"handful of chapters, not a whole book."
            )
    return warns


def check(campaign: str) -> tuple[int, list]:
    camp = paths.find_campaign(campaign)
    if not camp.exists():
        return 1, [f"campaign not found: {campaign}"]

    source_dir = camp / "source"
    index = camp / "source-index.md"

    if not source_dir.exists() and not index.exists():
        return 0, [f"{campaign}: not a lazy-corpus campaign (no source/ layer) — nothing to check"]

    problems = []

    if not index.exists():
        problems.append("source/ present but source-index.md is missing")
        indexed = set()
    else:
        indexed = indexed_ids(index.read_text(encoding="utf-8", errors="replace"))
        if not indexed:
            problems.append("source-index.md references no source/<id>.md files")

    on_disk = set()
    if source_dir.exists():
        on_disk = {p.stem for p in source_dir.glob("*.md")}
    else:
        problems.append("source-index.md present but source/ directory is missing")

    for cid in sorted(indexed - on_disk):
        problems.append(f"indexed chapter '{cid}' has no source/{cid}.md")
    for cid in sorted(on_disk - indexed):
        problems.append(f"orphan source file source/{cid}.md not in source-index.md")

    if not (camp / "arc.md").exists():
        problems.append("arc.md is missing (structured campaigns keep the full arc tree there)")

    return (1 if problems else 0), problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate lazy-corpus layout.")
    parser.add_argument("--campaign", required=True)
    args = parser.parse_args()

    code, messages = check(args.campaign)
    for m in messages:
        print(m)
    warns = size_warnings(args.campaign)
    for w in warns:
        print(w)
    if code == 0 and not any("nothing to check" in m for m in messages):
        suffix = f" ({len(warns)} oversized-chapter warning(s))" if warns else ""
        print(f"{args.campaign}: lazy-corpus layout OK{suffix}")
    return code


if __name__ == "__main__":
    sys.exit(main())
