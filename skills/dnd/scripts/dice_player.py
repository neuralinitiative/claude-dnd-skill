#!/usr/bin/env python3
"""dice_player.py — route a PC-attached dice roll through the display companion.

Mirrors `dice.py` syntax for the spec + advantage tokens, but instead of rolling
locally (or routing to the optional port-7777 physical-dice server), this POSTs
a `/dice-request` to the display companion at port 5001 and blocks until the
player taps Roll on their phone. The roll itself is rolled server-side with
`secrets.randbelow`, broadcast as a `dice:true` feed entry on the main display,
and printed to stdout here in a `dice.py`-shaped result line so any DM tooling
that consumes dice.py's stdout will keep working.

Usage:
    python3 dice_player.py d20+5 --player piper --label "Stealth check"
    python3 dice_player.py d20 adv --player mira --label "Initiative"
    python3 dice_player.py d20+3 dis --player piper --label "Concentration save"
    python3 dice_player.py 2d6+3 --player mira --label "Greataxe damage"
    python3 dice_player.py 1d100 --player piper --label "Wild Magic Surge" --dc 50

Requires `--player <name>` (the phone's bound character). Without a phone
target, use `dice.py` instead. Exits 1 on network error, 2 on phone timeout.

Spec parsing matches dice.py: NdM ± K (default N=1), with optional `adv`/`dis`
tokens as separate positional arguments.
"""

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

from paths import display_dir as _display_dir, runtime_dir as _runtime_dir
DISPLAY_DIR = str(_display_dir())
TOKEN_FILE  = os.path.join(str(_runtime_dir()), ".token")   # runtime state → update-safe dir
SCHEME_FILE = os.path.join(DISPLAY_DIR, ".scheme")          # launch marker → code dir
TIMEOUT     = 8.0

# Match the scheme the display server is using (HTTP default, HTTPS if --tls).
try:
    with open(SCHEME_FILE, encoding="utf-8") as f:
        _SCHEME = f.read().strip() or "http"
except FileNotFoundError:
    _SCHEME = "http"
_BASE       = f"{_SCHEME}://localhost:5001"
_DICE_REQ   = f"{_BASE}/dice-request"
_TEXT_LOG   = os.path.join(str(_runtime_dir()), "text_log.json")

_SSL_CTX: "ssl.SSLContext | None" = None
if _SCHEME == "https":
    _SSL_CTX = ssl.create_default_context()
    _SSL_CTX.check_hostname = False
    _SSL_CTX.verify_mode = ssl.CERT_NONE


def _read_token() -> str:
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def _parse_spec(raw: str) -> "tuple[str, int]":
    """Accept dice.py-style specs: d20, 1d20, 2d6+3, d100-5, d20+0.

    Returns (canonical_spec, modifier). Canonical spec is always NdM with N>=1.
    Modifier can be negative.
    """
    s = raw.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d*)d(\d+)([+-]\d+)?", s)
    if not m:
        raise ValueError(f"bad spec {raw!r} — expected forms like d20, 1d20+5, 2d6-1")
    n = int(m.group(1) or 1)
    sides = int(m.group(2))
    mod = int(m.group(3)) if m.group(3) else 0
    return f"{n}d{sides}", mod


def _post(url: str, body: dict, token: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-DND-Token"] = token
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _get(url: str, token: str) -> dict:
    headers = {"X-DND-Token": token} if token else {}
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _delete(url: str, token: str) -> None:
    headers = {"X-DND-Token": token} if token else {}
    req = urllib.request.Request(url, headers=headers, method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX)
    except Exception:
        pass


def _find_resolved_roll(character: str, spec: str, since_ts: float) -> "dict | None":
    """Scan text_log.json for the matching dice entry written since `since_ts`.

    The /dice-request flow doesn't push the resolved roll back through the
    request endpoint — the phone POSTs to /player-input/dice, which broadcasts
    and appends to text_log. The wrapper reads it back from the on-disk log.
    Match heuristic: most recent `dice:true` entry whose text starts with
    `<character> rolls <spec>`.
    """
    try:
        with open(_TEXT_LOG, encoding="utf-8") as f:
            log = json.load(f)
    except FileNotFoundError:
        return None
    # We can't compare timestamps because text_log entries don't carry them —
    # scan from newest backward and pick the first match.
    needle = f"{character} rolls {spec}"
    for entry in reversed(log):
        if entry.get("dice") and entry.get("text", "").startswith(needle):
            return entry
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Route a player-character dice roll through the display companion."
    )
    parser.add_argument("spec", help="Dice spec like d20, 2d6+3, 1d20-2")
    parser.add_argument("adv_dis", nargs="?", default="",
                        help="Optional advantage marker: adv, advantage, dis, disadvantage")
    parser.add_argument("--player", required=True, metavar="NAME",
                        help="Phone-bound character (case-insensitive). REQUIRED.")
    parser.add_argument("--label", default="", metavar="TEXT",
                        help='Human label shown on the phone (e.g. "Stealth check").')
    parser.add_argument("--dc", type=int, default=None, metavar="N",
                        help="Optional DC for informational display on the phone.")
    parser.add_argument("--timeout", type=int, default=600, metavar="SECONDS",
                        help="Seconds to wait for the player to roll (default 600).")
    args = parser.parse_args()

    try:
        spec, modifier = _parse_spec(args.spec)
    except ValueError as e:
        print(f"dice_player.py: {e}", file=sys.stderr)
        sys.exit(1)

    adv_token = args.adv_dis.lower()
    if adv_token in ("adv", "advantage"):
        advantage = "advantage"
    elif adv_token in ("dis", "disadvantage"):
        advantage = "disadvantage"
    elif adv_token == "":
        advantage = "normal"
    else:
        print(f"dice_player.py: unknown adv/dis token {args.adv_dis!r}", file=sys.stderr)
        sys.exit(1)

    token = _read_token()
    body = {
        "characters": [args.player],
        "spec": spec,
        "modifier": modifier,
        "advantage": advantage,
    }
    if args.label: body["label"] = args.label
    if args.dc is not None: body["dc"] = args.dc

    try:
        resp = _post(_DICE_REQ, body, token)
    except urllib.error.HTTPError as e:
        print(f"dice_player.py: HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"dice_player.py: network error: {e}", file=sys.stderr)
        sys.exit(1)

    request_id = resp.get("request_id", "")
    if not request_id:
        print("dice_player.py: server did not return a request_id", file=sys.stderr)
        sys.exit(1)

    print(f"dice_player.py: waiting on {args.player} ({spec}"
          f"{'+'+str(modifier) if modifier>0 else (str(modifier) if modifier<0 else '')}"
          f"{', '+advantage if advantage != 'normal' else ''}"
          f"{', DC '+str(args.dc) if args.dc is not None else ''}"
          f") — request_id={request_id}",
          file=sys.stderr)

    # Poll until the pending set drains (player rolled) or timeout.
    started = time.time()
    while time.time() - started < max(1, args.timeout):
        try:
            status = _get(f"{_DICE_REQ}/{request_id}", token)
        except Exception as e:
            print(f"dice_player.py: poll error ({e}) — retrying", file=sys.stderr)
            time.sleep(1.0)
            continue
        if status.get("complete"):
            break
        time.sleep(0.5)
    else:
        # Timeout: cancel the pending request so the phone doesn't stay locked.
        _delete(f"{_DICE_REQ}/{request_id}", token)
        print(f"dice_player.py: timeout after {args.timeout}s — request cancelled, phone unlocked.",
              file=sys.stderr)
        sys.exit(2)

    # Pull the resolved roll back from text_log for the dice.py-style stdout line.
    rolled = _find_resolved_roll(args.player, spec, started)
    if rolled is None:
        # Roll happened but we couldn't find it; still report success.
        print(f"dice_player.py: roll completed but text_log lookup failed", file=sys.stderr)
        sys.exit(0)

    # dice.py-style stdout: just the resolved text line, so any caller piping
    # this into another tool sees the same shape it would from dice.py.
    print(rolled.get("text", ""))


if __name__ == "__main__":
    main()
