#!/usr/bin/env python3
"""bump_version.py — bump the plugin version everywhere in one shot.

The version lives in five places; keeping them in lockstep by hand is
drift-prone. This updates them all (and optionally commits + tags):

  1. VERSION                          — source of truth
  2. .claude-plugin/plugin.json       — top-level "version"
  3. .claude-plugin/marketplace.json  — the `dm` plugin entry's "version"
  4. skills/dnd/SKILL.md              — the "vMAJOR.MINOR.PATCH ·" slash-picker prefix
  5. CHANGELOG.md                     — promote [Unreleased] → [X.Y.Z] — <date>

Usage:
    python3 scripts/bump_version.py 2.1.0
    python3 scripts/bump_version.py 2.1.0 --title "Spell tracker"
    python3 scripts/bump_version.py 2.1.0 --commit      # stage + commit the bump
    python3 scripts/bump_version.py 2.1.0 --tag         # commit + annotated tag vX.Y.Z
    python3 scripts/bump_version.py 2.1.0 --dry-run     # show changes, write nothing

Edits the working tree only unless --commit/--tag is given; never pushes.
Exit codes: 0 ok · 2 usage/validation error · 3 a file didn't match as expected.
"""
import argparse
import datetime
import pathlib
import re
import subprocess
import sys

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _repo_root() -> pathlib.Path:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, encoding="utf-8", text=True, check=True,
        )
        return pathlib.Path(r.stdout.strip())
    except Exception:
        return pathlib.Path(__file__).resolve().parents[1]


class BumpError(Exception):
    pass


def _sub_once(text: str, pattern: str, repl: str, *, where: str, count_expected: int = 1) -> str:
    new, n = re.subn(pattern, repl, text, flags=re.MULTILINE)
    if n != count_expected:
        raise BumpError(f"{where}: expected {count_expected} replacement(s), made {n} "
                        f"(pattern: {pattern!r})")
    return new


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def bump_version_file(root, old, new, dry):
    p = root / "VERSION"
    if p.read_text(encoding="utf-8").strip() != old:
        raise BumpError(f"VERSION holds {p.read_text(encoding='utf-8').strip()!r}, expected {old!r}")
    if not dry:
        p.write_text(new + "\n", encoding="utf-8")
    return "VERSION"


def bump_plugin_json(root, old, new, dry):
    p = root / ".claude-plugin" / "plugin.json"
    out = _sub_once(_read(p), rf'("version":\s*)"{re.escape(old)}"', rf'\g<1>"{new}"',
                    where="plugin.json")
    if not dry:
        p.write_text(out, encoding="utf-8")
    return ".claude-plugin/plugin.json"


def bump_marketplace_json(root, old, new, dry):
    # Only the dm plugin entry's version equals `old`; the marketplace's own
    # metadata.version is independent (e.g. 1.0.0), so a scoped single match.
    p = root / ".claude-plugin" / "marketplace.json"
    out = _sub_once(_read(p), rf'("version":\s*)"{re.escape(old)}"', rf'\g<1>"{new}"',
                    where="marketplace.json (plugin entry)")
    if not dry:
        p.write_text(out, encoding="utf-8")
    return ".claude-plugin/marketplace.json"


def bump_skill_description(root, old, new, dry):
    # The picker prefix is "vMAJOR.MINOR.PATCH ·" — the full release shows in the
    # slash-command picker so the exact running version is visible at a glance.
    p = root / "skills" / "dnd" / "SKILL.md"
    out, n = re.subn(r'(description:\s*"?)v\d+\.\d+(?:\.\d+)?(\s*·)',
                     rf'\g<1>v{new}\g<2>', _read(p))
    if n != 1:
        raise BumpError(f"SKILL.md: expected 1 'vX.Y[.Z] ·' prefix, found {n}")
    if not dry:
        p.write_text(out, encoding="utf-8")
    return f"skills/dnd/SKILL.md (picker prefix → v{new})"


def bump_changelog(root, old, new, date, title, dry):
    p = root / "CHANGELOG.md"
    text = _read(p)
    m = re.search(r"(?ms)^## \[Unreleased\][ \t]*\n(.*?)(?=^## \[)", text)
    if not m:
        raise BumpError("CHANGELOG.md: no '## [Unreleased]' section found")
    body = m.group(1).strip("\n")
    header = f"## [{new}] — {date}" + (f" — {title}" if title else "")
    promoted = body if body.strip() else "_No notes recorded under [Unreleased]._"
    replacement = f"## [Unreleased]\n\n{header}\n\n{promoted}\n\n"
    out = text[:m.start()] + replacement + text[m.end():]
    note = "" if body.strip() else "  ⚠ [Unreleased] was empty — add notes!"
    if not dry:
        p.write_text(out, encoding="utf-8")
    return f"CHANGELOG.md (promoted [Unreleased] → [{new}]){note}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Bump the plugin version everywhere.")
    ap.add_argument("version", help="new version, e.g. 2.1.0 (semver MAJOR.MINOR.PATCH)")
    ap.add_argument("--date", help="release date YYYY-MM-DD (default: today)")
    ap.add_argument("--title", help="CHANGELOG entry title (optional)")
    ap.add_argument("--commit", action="store_true", help="git add + commit the bump")
    ap.add_argument("--tag", action="store_true", help="commit and annotated-tag vX.Y.Z")
    ap.add_argument("--dry-run", action="store_true", help="show changes, write nothing")
    args = ap.parse_args()

    new = args.version.strip().lstrip("v")
    if not SEMVER.match(new):
        print(f"error: {new!r} is not MAJOR.MINOR.PATCH semver", file=sys.stderr)
        return 2
    if args.tag:
        args.commit = True
    date = args.date or datetime.date.today().isoformat()

    root = _repo_root()
    old = (root / "VERSION").read_text(encoding="utf-8").strip()
    if new == old:
        print(f"error: VERSION is already {old}", file=sys.stderr)
        return 2

    print(f"Bumping {old} → {new}" + (" (dry-run)" if args.dry_run else "") + ":")
    steps = [
        bump_version_file(root, old, new, args.dry_run),
        bump_plugin_json(root, old, new, args.dry_run),
        bump_marketplace_json(root, old, new, args.dry_run),
        bump_skill_description(root, old, new, args.dry_run),
        bump_changelog(root, old, new, date, args.title, args.dry_run),
    ]
    for s in steps:
        print(f"  ✓ {s}")

    if args.dry_run:
        print("\nDry run — nothing written.")
        return 0

    files = ["VERSION", ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
             "skills/dnd/SKILL.md", "CHANGELOG.md"]
    if args.commit:
        subprocess.run(["git", "-C", str(root), "add", *files], check=True)
        msg = f"chore(release): {new}" + (f" — {args.title}" if args.title else "")
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", msg], check=True)
        print(f"\n✓ Committed: {msg}")
    if args.tag:
        tag = f"v{new}"
        subprocess.run(["git", "-C", str(root), "tag", "-a", tag, "-m",
                        f"{new}" + (f" — {args.title}" if args.title else "")], check=True)
        print(f"✓ Tagged: {tag}")

    print("\nNext: review the CHANGELOG entry, then "
          + ("push (git push && git push --tags)." if args.tag
             else "commit (or re-run with --tag) and push."))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BumpError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(3)
