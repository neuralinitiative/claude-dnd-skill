"""
path_config.py — view and configure DND_CAMPAIGN_ROOT.

Usage:
    python3 path_config.py                  # show current paths
    python3 path_config.py set <path>       # persist DND_CAMPAIGN_ROOT
    python3 path_config.py reset            # remove persisted value

Persists by writing the env var to the user's shell rc on macOS/Linux, or via
`setx` on Windows. The change does not affect the parent shell — users must
open a new shell or `source` their rc to pick it up.
"""
import argparse
import os
import pathlib
import re
import subprocess
import sys

from utf8io import read_text, TextDecodeError

from paths import _root, campaigns_dir, characters_dir

SHELLRC_CANDIDATES = [
    pathlib.Path("~/.zshrc").expanduser(),
    pathlib.Path("~/.bashrc").expanduser(),
    pathlib.Path("~/.bash_profile").expanduser(),
]
EXPORT_RE = re.compile(r'^\s*export\s+DND_CAMPAIGN_ROOT=.*\n?', re.MULTILINE)


def _is_windows() -> bool:
    return os.name == "nt"


def _shellrc() -> pathlib.Path:
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        return pathlib.Path("~/.zshrc").expanduser()
    if "bash" in shell:
        for p in (pathlib.Path("~/.bashrc").expanduser(),
                  pathlib.Path("~/.bash_profile").expanduser()):
            if p.exists():
                return p
    for p in SHELLRC_CANDIDATES:
        if p.exists():
            return p
    return pathlib.Path("~/.zshrc").expanduser()


def show() -> None:
    raw = os.environ.get("DND_CAMPAIGN_ROOT", "").strip()
    root = _root()
    cdir = campaigns_dir()
    chdir = characters_dir()
    n_campaigns = len([p for p in cdir.iterdir() if p.is_dir()]) if cdir.exists() else 0
    n_chars = len(list(chdir.glob("*.md"))) if chdir.exists() else 0
    source = "from DND_CAMPAIGN_ROOT" if raw else "default — DND_CAMPAIGN_ROOT not set"
    print(f"Campaign root: {root}  ({source})")
    print(f"  campaigns/   → {cdir}  ({n_campaigns} campaigns)")
    print(f"  characters/  → {chdir}  ({n_chars} characters)")
    if not raw:
        print("  Tip: /dnd path <new-path> to move data (e.g. ~/Dropbox/dnd)")


def _set_windows(target: pathlib.Path) -> None:
    # setx writes to the user environment in the registry; effective in new shells.
    result = subprocess.run(
        ["setx", "DND_CAMPAIGN_ROOT", str(target)],
        capture_output=True, encoding="utf-8", text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr or "setx failed\n")
        raise SystemExit(result.returncode)
    print(f"Set DND_CAMPAIGN_ROOT={target}")
    print("Persisted via setx (user environment).")
    print("Open a new terminal for the change to take effect.")


def _set_unix(target: pathlib.Path) -> None:
    rc = _shellrc()
    line = f'export DND_CAMPAIGN_ROOT="{target}"'
    try:
        # Lossless read: the user's shell rc may predate the UTF-8 sweep and
        # hold GBK bytes — transcode once instead of writing U+FFFD into it.
        existing = read_text(rc) if rc.exists() else ""
    except (OSError, TextDecodeError) as e:
        sys.stderr.write(f"[path_config] {rc}: {e} — refusing to modify shell rc\n")
        raise SystemExit(1)
    if EXPORT_RE.search(existing):
        new_text = EXPORT_RE.sub(line + "\n", existing)
    else:
        sep = "" if existing.endswith("\n") or not existing else "\n"
        new_text = f"{existing}{sep}{line}\n"
    rc.write_text(new_text, encoding="utf-8")
    print(f"Set DND_CAMPAIGN_ROOT={target}")
    print(f"Persisted to {rc}")
    print(f'Run: export DND_CAMPAIGN_ROOT="{target}"  (or open a new shell)')


def set_path(new: str) -> None:
    target = pathlib.Path(new).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    if _is_windows():
        _set_windows(target)
    else:
        _set_unix(target)


def _reset_windows() -> None:
    # `setx VAR ""` leaves an empty value; use reg delete to remove entirely.
    result = subprocess.run(
        ["reg", "delete", "HKCU\\Environment", "/F", "/V", "DND_CAMPAIGN_ROOT"],
        capture_output=True, encoding="utf-8", text=True,
    )
    if result.returncode == 0:
        print("Removed DND_CAMPAIGN_ROOT from user environment.")
        print("Open a new terminal for the change to take effect.")
    else:
        # reg delete returns nonzero if the value didn't exist
        print("No persisted value found in user environment.")


def _reset_unix() -> None:
    rc = _shellrc()
    if not rc.exists():
        print(f"No persisted value (no {rc}).")
        return
    try:
        text = read_text(rc)
    except (OSError, TextDecodeError) as e:
        sys.stderr.write(f"[path_config] {rc}: {e} — refusing to modify shell rc\n")
        raise SystemExit(1)
    if not EXPORT_RE.search(text):
        print(f"No DND_CAMPAIGN_ROOT line found in {rc}.")
        return
    cleaned = EXPORT_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    rc.write_text(cleaned, encoding="utf-8")
    print(f"Removed DND_CAMPAIGN_ROOT from {rc}.")
    print("Run: unset DND_CAMPAIGN_ROOT  (or open a new shell)")


def reset() -> None:
    if _is_windows():
        _reset_windows()
    else:
        _reset_unix()


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("show")
    s = sub.add_parser("set"); s.add_argument("path")
    sub.add_parser("reset")
    args = p.parse_args()
    if args.cmd == "set":
        set_path(args.path)
    elif args.cmd == "reset":
        reset()
    else:
        show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
