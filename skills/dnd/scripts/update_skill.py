"""
update_skill.py — check for and apply updates to the dnd skill.

Usage:
    python3 update_skill.py            # check, show diff, prompt to pull
    python3 update_skill.py --check    # check only, no pull
    python3 update_skill.py --yes      # pull without prompting

Install-mode aware:
  * Plugin install (managed by Claude Code) — defers to `/plugin update dm`
    rather than git-pulling under the plugin manager's feet.
  * Dev clone / legacy standalone (a plain git checkout) — fast-forwards from
    origin. Refuses if the working tree is dirty; uses --ff-only so it never
    silently merges divergent history.
"""
from __future__ import annotations  # PEP 604 (X | None) annotations on Python 3.9

import argparse
import os
import pathlib
import subprocess
import sys
import urllib.request

from paths import skill_root as _skill_root

# Canonical VERSION on the published marketplace branch — used to tell a plugin
# install whether it's behind. 404s gracefully before the org transfer lands.
# Override with DND_UPDATE_VERSION_URL (e.g. to test, or track a fork).
_REMOTE_VERSION_URL = os.environ.get("DND_UPDATE_VERSION_URL", "").strip() or (
    "https://raw.githubusercontent.com/neuralinitiative/claude-dnd-skill/main/VERSION"
)

# The skill dir holds SKILL.md + scripts/data/display. The git checkout root is
# the repo root: skill dir is <repo>/skills/dnd, so the repo is two levels up.
# (Legacy standalone installs had the repo == skill dir; walk up to find .git.)
SKILL_DIR = _skill_root()


def _find_git_root(start: pathlib.Path) -> pathlib.Path | None:
    for d in (start, *start.parents):
        if (d / ".git").exists():
            return d
    return None


GIT_ROOT = _find_git_root(SKILL_DIR)

# Heuristic: a Claude-Code-managed plugin install exposes CLAUDE_PLUGIN_ROOT,
# or lives under `.claude/plugins/` (the canonical Claude Code install path).
# The path check is the fallback when CLAUDE_PLUGIN_ROOT isn't exported into
# the subprocess; scoped to `.claude/plugins/` so a dev tree that happens to
# contain a directory named `plugins` won't false-positive into plugin mode.
PLUGIN_MODE = bool(os.environ.get("CLAUDE_PLUGIN_ROOT")) or (
    ".claude" + os.sep + "plugins" + os.sep in str(SKILL_DIR)
)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(GIT_ROOT), *args],
        capture_output=True, encoding="utf-8", text=True, check=check,
    )


def _read_local_version() -> str:
    f = (GIT_ROOT or SKILL_DIR) / "VERSION"
    if not f.exists():
        return "(no VERSION file — pre-1.6 baseline)"
    return f.read_text(encoding="utf-8").strip()


def _read_remote_version(branch: str) -> str:
    """Read VERSION from origin/<branch> without checking it out."""
    try:
        r = git("show", f"origin/{branch}:VERSION", check=False)
        if r.returncode == 0:
            return r.stdout.strip()
        return "(no VERSION on remote)"
    except subprocess.CalledProcessError:
        return "(unreadable)"


# ── Plugin-install version awareness ──────────────────────────────────────
# In a plugin install the VERSION file sits at the plugin/repo root, not inside
# skills/dnd, and there's no git checkout to diff. Resolve the local version
# from the plugin root and compare it against the published marketplace VERSION.

def _plugin_local_version() -> str:
    candidates = []
    env = os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip()
    if env:
        candidates.append(pathlib.Path(env).expanduser() / "VERSION")
    candidates.append(SKILL_DIR.parent.parent / "VERSION")  # skills/dnd → plugin root
    candidates.append(SKILL_DIR / "VERSION")
    for c in candidates:
        try:
            if c.exists():
                return c.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return "unknown"


def _fetch_remote_version(timeout: float = 4.0):
    """Latest published VERSION, or None if unreachable (offline / not yet published)."""
    try:
        with urllib.request.urlopen(_REMOTE_VERSION_URL, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace").strip()
    except Exception:
        return None


def _ver_tuple(v: str):
    out = []
    for part in v.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def _is_newer(remote: str, local: str) -> bool:
    try:
        return _ver_tuple(remote) > _ver_tuple(local)
    except Exception:
        return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true", help="check only, do not pull")
    p.add_argument("--yes", action="store_true", help="pull without prompting")
    args = p.parse_args()

    if PLUGIN_MODE:
        local_ver = _plugin_local_version()
        remote_ver = _fetch_remote_version()
        print(f"D&D plugin (dm) — installed version {local_ver}.")
        if remote_ver is None:
            print("Couldn't reach the marketplace to check for a newer release "
                  "(offline, or the repo isn't published there yet).")
            print("Update any time with:  /plugin update dm")
        elif _is_newer(remote_ver, local_ver):
            print(f"⬆  Update available: {local_ver} → {remote_ver}")
            print("   Update with:  /plugin update dm")
            print("   Then /reload-plugins (or restart Claude Code) to load it.")
        else:
            print(f"✓  Up to date (latest published is {remote_ver}).")
        print("\n(Plugin code is managed by Claude Code; `/dnd update` reports status "
              "but defers the actual update to /plugin update.)")
        return 0

    if GIT_ROOT is None:
        print(f"Skill at {SKILL_DIR} is not a git checkout and not a plugin install.",
              file=sys.stderr)
        print(
            "If you installed manually, reinstall via:\n"
            "    git clone https://github.com/neuralinitiative/claude-dnd-skill\n"
            "or install it as a plugin (see README).",
            file=sys.stderr,
        )
        return 2

    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    local_ver = _read_local_version()
    print(f"Skill location: {SKILL_DIR}  (branch: {branch}, version: {local_ver})")

    dirty = git("status", "--porcelain").stdout.strip()
    if dirty:
        print("Local changes detected — refusing to update:", file=sys.stderr)
        print(dirty, file=sys.stderr)
        print("\nCommit, stash, or discard your changes and re-run.", file=sys.stderr)
        return 3

    git("fetch", "--quiet", "origin", branch)
    local = git("rev-parse", "HEAD").stdout.strip()
    remote = git("rev-parse", f"origin/{branch}").stdout.strip()

    if local == remote:
        print(f"Up to date with origin/{branch} ({local[:7]}).")
        return 0

    behind = git("rev-list", "--count", f"HEAD..origin/{branch}").stdout.strip()
    log = git("log", "--oneline", f"HEAD..origin/{branch}").stdout.strip()
    remote_ver = _read_remote_version(branch)
    print(f"Local:  {local[:7]}  (version {local_ver})")
    print(f"Remote: {remote[:7]}  (version {remote_ver})")
    print(f"\n{behind} commits behind origin/{branch}:")
    print(log)
    if local_ver != remote_ver and not local_ver.startswith("("):
        print(f"\nVersion change: {local_ver} → {remote_ver}  "
              f"(see CHANGELOG.md after update for details)")

    if args.check:
        return 0

    if not args.yes:
        try:
            answer = input("\nPull now? (y/N) ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"y", "yes"}:
            print("Skipped.")
            return 0

    pull = git("pull", "--ff-only", "origin", branch, check=False)
    sys.stdout.write(pull.stdout)
    sys.stderr.write(pull.stderr)
    if pull.returncode != 0:
        print(
            "\nFast-forward failed — resolve manually with git in the skill directory.",
            file=sys.stderr,
        )
        return pull.returncode

    print(f"\nUpdated to {remote[:7]}. Restart Claude Code to load new skill files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
