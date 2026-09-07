#!/usr/bin/env python3
"""scan_plugin.py — run the HOL plugin scanner against this repo, on demand.

The awesome-ai-plugins catalog scans listed plugins and recommends adding
their GitHub Action to CI. We deliberately do not: a composite action runs
with full access to the runner on every push, and a standing third-party
dependency in CI is a bet on who owns that repository years from now. The
trust-score reduction is the price, and it is a fair one.

This is the alternative. Same scanner, run when we ask for it, before a
release or after touching anything the scanner looks at. Nothing runs it
automatically and it is not wired into any workflow.

The scanner wheel is pinned by version AND SHA256. That matters more than it
looks: the upstream action verifies hashes too, but its expected hashes live
in the action's own repository, so they defend the PyPI leg and not a
compromise of the publisher. Pinning here means a swapped artifact fails
loudly instead of running. Dependencies of the wheel resolve normally, so
this is a meaningful check on the scanner and not a hermetic build.

Updating the pin (do both, or the hash check fails):
    pip index versions plugin-scanner
    curl -s https://pypi.org/pypi/plugin-scanner/json \
      | python3 -c "import json,sys; d=json.load(sys.stdin); v=d['info']['version']; \
        print(v, [f['digests']['sha256'] for f in d['releases'][v] \
        if f['packagetype']=='bdist_wheel'][0])"

Usage:
    python3 scripts/scan_plugin.py                  # scan the repo root
    python3 scripts/scan_plugin.py --fail-on medium # stricter than the catalog
    python3 scripts/scan_plugin.py --json out.json  # keep the machine-readable report
    python3 scripts/scan_plugin.py --keep-venv      # reuse the env on the next run

Exit codes: 0 clean · 1 findings at or above --fail-on, or score below
              --min-score · 2 usage error
            · 3 the pinned wheel failed its integrity check
"""
import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

# Pinned deliberately. See the module docstring before changing either line.
SCANNER_VERSION = "3.0.114"
SCANNER_SHA256 = "45e17a4f391083a674ed1c89bcd4638637056cd1a7000270278e87557e828001"

SEVERITIES = ("critical", "high", "medium", "low", "info")
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
# Deliberately OUTSIDE the repo. A venv under the repo root gets scanned along
# with it, and the scanner flags its own bundled assets: 21 spurious "hardcoded
# secret" highs, dropping a clean tree from 100/A to 80/B. Keeping it out of
# the tree also means no .gitignore entry to forget.
VENV_DIR = pathlib.Path(tempfile.gettempdir()) / "claude-dnd-skill-scanner-venv"


def _venv_bin(venv: pathlib.Path, name: str) -> pathlib.Path:
    """Path to an executable inside the venv, Windows layout included."""
    win = venv / "Scripts"
    if win.exists():
        exe = win / (name + ".exe")
        return exe if exe.exists() else win / name
    return venv / "bin" / name


def _venv_python(venv: pathlib.Path) -> pathlib.Path:
    return _venv_bin(venv, "python")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run quietly, but print everything if it fails.

    pip emits cache warnings on stderr that have nothing to do with us;
    swallowing them unconditionally would also swallow a real failure.
    """
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise subprocess.CalledProcessError(proc.returncode, cmd)
    return proc


def _fetch_and_verify(py: pathlib.Path, dest: pathlib.Path) -> pathlib.Path:
    """Download the pinned wheel and refuse to install a different one."""
    _run([str(py), "-m", "pip", "download", "--only-binary=:all:", "--no-deps",
          "--dest", str(dest), f"plugin-scanner=={SCANNER_VERSION}"])

    wheels = sorted(dest.glob("plugin_scanner-*.whl"))
    if len(wheels) != 1:
        print(f"expected exactly one scanner wheel, found {len(wheels)}", file=sys.stderr)
        raise SystemExit(3)

    actual = hashlib.sha256(wheels[0].read_bytes()).hexdigest()
    if actual != SCANNER_SHA256:
        print("Pinned scanner wheel failed its integrity check.", file=sys.stderr)
        print(f"  expected {SCANNER_SHA256}", file=sys.stderr)
        print(f"  actual   {actual}", file=sys.stderr)
        print("Do not install this. Either the pin is stale (update BOTH the version",
              file=sys.stderr)
        print("and the hash) or the artifact is not what it claims to be.", file=sys.stderr)
        raise SystemExit(3)

    return wheels[0]


def _ensure_scanner(keep: bool) -> pathlib.Path:
    """Return the scanner executable, building the env if it is not usable.

    Completeness is decided by the scanner binary, NOT by the venv directory
    existing. An aborted install (a failed hash check, an interrupted run)
    leaves the directory behind with no scanner in it, and treating that as
    ready turns the next --keep-venv run into a FileNotFoundError traceback.
    """
    scanner = _venv_bin(VENV_DIR, "plugin-scanner")
    if keep and scanner.exists():
        return scanner

    if VENV_DIR.exists():
        shutil.rmtree(VENV_DIR)
    try:
        _run([sys.executable, "-m", "venv", str(VENV_DIR)])
        py = _venv_python(VENV_DIR)
        _run([str(py), "-m", "pip", "install", "--upgrade", "pip"])
        with tempfile.TemporaryDirectory() as tmp:
            wheel = _fetch_and_verify(py, pathlib.Path(tmp))
            print(f"scanner {SCANNER_VERSION} verified against its pinned hash",
                  flush=True)
            _run([str(py), "-m", "pip", "install", str(wheel)])
    except BaseException:
        # Never leave a half-built env for the next run to trip over.
        shutil.rmtree(VENV_DIR, ignore_errors=True)
        raise

    if not scanner.exists():
        print("scanner installed but its executable is missing", file=sys.stderr)
        raise SystemExit(3)
    return scanner


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run the pinned HOL plugin scanner against this repo.")
    ap.add_argument("path", nargs="?", default=str(REPO_ROOT),
                    help="directory to scan (default: repo root)")
    ap.add_argument("--fail-on", default="high", choices=SEVERITIES,
                    help="lowest severity that fails the run (default: high, "
                         "which is what the catalog gate uses)")
    ap.add_argument("--min-score", type=int, default=80,
                    help="minimum score to pass (default: 80, the catalog's "
                         "threshold). The gate is score AND severity, so "
                         "checking only findings can report clean on a tree "
                         "the catalog would still reject.")
    ap.add_argument("--json", dest="json_out",
                    help="also write the full JSON report here")
    ap.add_argument("--keep-venv", action="store_true",
                    help="reuse the cached scanner env instead of rebuilding it")
    args = ap.parse_args()

    target = pathlib.Path(args.path).resolve()
    if not target.is_dir():
        print(f"not a directory: {target}", file=sys.stderr)
        return 2

    scanner = _ensure_scanner(args.keep_venv)

    with tempfile.TemporaryDirectory() as tmp:
        report = pathlib.Path(tmp) / "report.json"
        # The scanner exits non-zero on its own threshold; we read the report
        # instead so --fail-on is ours to decide and the summary always prints.
        # The wheel installs a `plugin-scanner` console script. The package
        # itself has no __main__, so `python -m codex_plugin_scanner` fails.
        subprocess.run([str(scanner), "scan", str(target),
                        "--format", "json", "--output", str(report)],
                       stdout=subprocess.DEVNULL, check=False)
        if not report.is_file():
            print("scanner produced no report", file=sys.stderr)
            return 3
        data = json.loads(report.read_text(encoding="utf-8"))
        if args.json_out:
            pathlib.Path(args.json_out).write_text(
                json.dumps(data, indent=2), encoding="utf-8")

    counts = data.get("summary", {}).get("findings", {})
    print(f"score {data.get('score')} ({data.get('grade')})  "
          + "  ".join(f"{s}:{counts.get(s, 0)}" for s in SEVERITIES))

    cutoff = SEVERITIES.index(args.fail_on)
    blocking = sum(counts.get(s, 0) for s in SEVERITIES[:cutoff + 1])
    score = data.get("score")

    failed = False
    if blocking:
        print(f"{blocking} finding(s) at or above {args.fail_on}", file=sys.stderr)
        failed = True
    if isinstance(score, (int, float)) and score < args.min_score:
        print(f"score {score} is below {args.min_score}", file=sys.stderr)
        failed = True
    if failed:
        print("Re-run with --json report.json to see the detail.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
