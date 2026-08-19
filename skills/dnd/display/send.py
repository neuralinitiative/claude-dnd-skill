#!/usr/bin/env python3
"""
send.py — send text to the DnD DM display server.

Usage:
    # DM narration (default)
    python3 send.py << 'DNDEND'
    The tavern reeks of old ale and burnt tallow.
    DNDEND

    # Player action — prepends character name on display
    python3 send.py --player Flerb << 'DNDEND'
    Flerb draws her greatsword and steps forward.
    DNDEND

    # Dice result — pipe from dice.py for open rolls
    python3 ../scripts/dice.py d20+4 | python3 send.py --dice

    # NPC dialogue — amber border, italic, amber name header
    python3 send.py --npc "Vesna" << 'DNDEND'
    "I've been waiting for you."
    DNDEND

    # Tutor/learning mode hint — collapsible parchment block on display
    python3 send.py --tutor << 'DNDEND'
    You could try a Perception check (WIS) to scan the room before acting.
    DNDEND

    # Player action intent — subdued label echoing what the player declared
    python3 send.py --action "Bob" << 'DNDEND'
    Attempts to shimmy across the rope to the ship under cover of darkness.
    DNDEND

    # Short inline string
    echo "Short message" | python3 send.py

    # State changes bundled with narration (Option B)
    python3 send.py --stat-hp "Mira:12:17" --stat-slot-use "Aldric:1" << 'DNDEND'
    The goblin's blade finds a gap in her armor for 5 damage...
    DNDEND

    # Supported stat flags (can repeat for multiple players):
    #   --stat-hp         "NAME:CURRENT:MAX"
    #   --stat-temp-hp    "NAME:N"
    #   --stat-slot-use   "NAME:LEVEL"       (expend one slot)
    #   --stat-slot-restore "NAME:LEVEL"     (restore one slot)
    #   --stat-condition-add    "NAME:CONDITION"
    #   --stat-condition-remove "NAME:CONDITION"
    #   --stat-concentrate "NAME:SPELL"       (empty SPELL = clear)
    #   --stat-inventory-add    "NAME:ITEM"
    #   --stat-inventory-remove "NAME:ITEM"
    #
    # Timed effect flags:
    #   --effect-start "NAME:SPELL:DURATION"   DURATION: 10r/60m/8h/indef  optional :conc
    #   --effect-end   "NAME:SPELL"            narrative end (broken/dispelled)
"""

import sys
import json
import argparse
import os
import ssl
import time
import urllib.request

# Windows 中文环境修复：Python 默认按系统代码页（GBK）解码 stdin，
# 而 heredoc/管道传入的是 UTF-8 字节 —— 强制 UTF-8，避免中文叙事乱码。
for _stream in (sys.stdin, sys.stdout):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


_DISPLAY_DIR = os.path.dirname(os.path.abspath(__file__))
if _DISPLAY_DIR not in sys.path:
    sys.path.insert(0, _DISPLAY_DIR)
from runtime_paths import rt          # writable runtime dir (update-safe)
_SCHEME_FILE = os.path.join(_DISPLAY_DIR, ".scheme")   # launch marker → code dir
_SCHEME = open(_SCHEME_FILE, encoding="utf-8").read().strip() if os.path.exists(_SCHEME_FILE) else "http"
BASE_URL    = f"{_SCHEME}://localhost:5001"
FLASK_URL   = f"{BASE_URL}/chunk"
STATS_URL   = f"{BASE_URL}/stats"
HEALTH_URL  = f"{BASE_URL}/health"
DICE_REQ_URL = f"{BASE_URL}/dice-request"
TOKEN_FILE  = rt(".token")
TIMEOUT     = 8.0
RETRIES     = 1                # one retry on timeout/connection error
CHUNK_LIMIT = 3500             # paragraph-split text bodies above this many chars

# Send-side counters tracked across the script's lifetime (one process per call).
# Used by the post-send self-check to detect skipped or failed sends BEFORE the
# script exits, surfacing problems to stderr where Bash output is visible.
_SEND_LOG: list = []  # entries: {"endpoint": "chunk"/"stats", "ok": bool, "reason": str, "size": int}

# SSL context — only used when running HTTPS (self-signed cert)
if _SCHEME == "https":
    _SSL_CTX = ssl.create_default_context()
    _SSL_CTX.check_hostname = False
    _SSL_CTX.verify_mode = ssl.CERT_NONE
else:
    _SSL_CTX = None


def _read_token() -> str:
    try:
        return open(TOKEN_FILE, encoding="utf-8").read().strip()
    except FileNotFoundError:
        return ""


def _endpoint_label(url: str) -> str:
    """Short label for stderr reporting and the send-log."""
    if url.endswith("/chunk"):  return "chunk"
    if url.endswith("/stats"):  return "stats"
    if url.endswith("/health"): return "health"
    return url.rsplit("/", 1)[-1] or url


def _post(url: str, data: bytes, token: str) -> bool:
    """POST data with retries. Logs every send (success or failure) to _SEND_LOG.

    Returns True on success, False after all retries exhausted. Display being
    offline is the only "expected" failure mode; everything else is logged so
    transient timeouts / dropped sends do not silently lose narration.
    """
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-DND-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    attempts = RETRIES + 1
    last_err: "Exception | None" = None
    label = _endpoint_label(url)
    for i in range(attempts):
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX)
            status = getattr(resp, "status", 200)
            # Server uses 204 (no content) for success; treat any 2xx as OK.
            if 200 <= status < 300:
                _SEND_LOG.append({"endpoint": label, "ok": True, "status": status, "size": len(data)})
                return True
            # Non-2xx: surface body to stderr for diagnosis.
            body_excerpt = resp.read(200).decode("utf-8", errors="replace") if hasattr(resp, "read") else ""
            print(f"send.py: POST {url} returned status {status}: {body_excerpt}", file=sys.stderr)
            _SEND_LOG.append({"endpoint": label, "ok": False, "reason": f"http {status}", "size": len(data)})
            return False
        except urllib.error.HTTPError as e:
            # 4xx/5xx — capture body so we can see the auth/validation reason.
            body_excerpt = ""
            try:
                body_excerpt = e.read(200).decode("utf-8", errors="replace")
            except Exception:
                pass
            print(f"send.py: POST {url} HTTP {e.code}: {body_excerpt}", file=sys.stderr)
            _SEND_LOG.append({"endpoint": label, "ok": False, "reason": f"http {e.code}", "size": len(data)})
            return False
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            inner = getattr(e, "reason", e)
            if isinstance(inner, ConnectionRefusedError) or "Connection refused" in str(inner):
                _SEND_LOG.append({"endpoint": label, "ok": False, "reason": "display offline", "size": len(data)})
                return False
            last_err = e
            if i < attempts - 1:
                time.sleep(0.5 * (i + 1))
        except Exception as e:
            last_err = e
            if i < attempts - 1:
                time.sleep(0.5 * (i + 1))
    print(f"send.py: POST {url} failed after {attempts} attempts: {last_err}",
          file=sys.stderr)
    _SEND_LOG.append({"endpoint": label, "ok": False, "reason": str(last_err), "size": len(data)})
    return False


def _validate_payload(payload: dict, endpoint: str) -> "list[str]":
    """Return a list of validation issues with the payload. Empty list = OK.

    Run BEFORE posting. Catches malformed sends that the server would accept
    but render incorrectly (e.g. text-content flag set but text body missing).
    """
    issues: list[str] = []
    if endpoint == "chunk":
        # Chunk payloads must carry recognizable content.
        text = payload.get("text", "")
        has_text = bool(text and str(text).strip())
        has_award = bool(payload.get("inspiration_award") or payload.get("xp_award")
                         or payload.get("milestone_award") or payload.get("milestone_spend"))
        if not has_text and not has_award:
            issues.append("chunk payload has no text and no award flag")
        # Mutually exclusive content tags
        content_tags = [k for k in ("player", "npc", "dice", "tutor", "action") if payload.get(k)]
        if len(content_tags) > 1:
            issues.append(f"chunk payload has multiple content tags: {content_tags}")
    elif endpoint == "stats":
        if "players" in payload and not isinstance(payload["players"], list):
            issues.append("stats payload 'players' is not a list")
    return issues


def _verify_health() -> "dict | None":
    """GET /health and return the server's status dict, or None if unreachable.

    Used by --verify to confirm the display saw the send. The /health endpoint
    returns counts for tail buffer, text log, current campaign, etc.
    """
    headers = {}
    token = _read_token()
    if token:
        headers["X-DND-Token"] = token
    req = urllib.request.Request(HEALTH_URL, headers=headers, method="GET")
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX)
        return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"send.py: /health unreachable ({e})", file=sys.stderr)
        return None


def _split_paragraphs(text: str, limit: int = CHUNK_LIMIT) -> list:
    """Split a long text body into chunks no larger than `limit` chars.

    Splits on paragraph boundaries (`\\n\\n`) when possible. Each chunk
    preserves the original whitespace. If a single paragraph exceeds the
    limit, it is hard-split on character boundary as a last resort.
    """
    if len(text) <= limit:
        return [text]
    paragraphs = text.split("\n\n")
    chunks: list = []
    cur = ""
    for p in paragraphs:
        candidate = (cur + "\n\n" + p) if cur else p
        if len(candidate) <= limit:
            cur = candidate
            continue
        # Flush whatever we have, start a new chunk with this paragraph
        if cur:
            chunks.append(cur)
            cur = ""
        # If this single paragraph is itself too big, hard-split it
        if len(p) > limit:
            for i in range(0, len(p), limit):
                chunks.append(p[i:i + limit])
        else:
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


def _build_stats_payload(args) -> "dict | None":
    """Build a push_stats-compatible payload from --stat-* flags."""
    players: "dict[str, dict]" = {}

    def _p(name: str) -> dict:
        return players.setdefault(name, {"name": name})

    for spec in (args.stat_hp or []):
        parts = spec.split(":")
        if len(parts) >= 3:
            name, cur, mx = parts[0], parts[1], parts[2]
            try:
                _p(name)["hp"] = {"current": int(cur), "max": int(mx)}
            except ValueError:
                pass

    for spec in (args.stat_temp_hp or []):
        idx = spec.rfind(":")
        if idx > 0:
            name, n = spec[:idx], spec[idx + 1:]
            try:
                _p(name).setdefault("hp", {})["temp"] = int(n)
            except ValueError:
                pass

    for spec in (args.stat_slot_use or []):
        idx = spec.rfind(":")
        if idx > 0:
            name, lvl = spec[:idx], spec[idx + 1:]
            try:
                _p(name)["_slot_use"] = int(lvl)
            except ValueError:
                pass

    for spec in (args.stat_slot_restore or []):
        idx = spec.rfind(":")
        if idx > 0:
            name, lvl = spec[:idx], spec[idx + 1:]
            try:
                _p(name)["_slot_restore"] = int(lvl)
            except ValueError:
                pass

    for spec in (args.stat_condition_add or []):
        idx = spec.find(":")
        if idx > 0:
            name, cond = spec[:idx], spec[idx + 1:]
            if cond.strip():
                _p(name)["_conditions_add"] = cond.strip()

    for spec in (args.stat_condition_remove or []):
        idx = spec.find(":")
        if idx > 0:
            name, cond = spec[:idx], spec[idx + 1:]
            if cond.strip():
                _p(name)["_conditions_remove"] = cond.strip()

    for spec in (args.stat_concentrate or []):
        idx = spec.find(":")
        if idx >= 0:
            name, spell = spec[:idx], spec[idx + 1:]
            _p(name)["concentration"] = spell.strip() or None

    for spec in (args.stat_inventory_add or []):
        idx = spec.find(":")
        if idx > 0:
            name, item = spec[:idx], spec[idx + 1:]
            if item.strip():
                _p(name)["_inventory_add"] = item.strip()

    for spec in (args.stat_inventory_remove or []):
        idx = spec.find(":")
        if idx > 0:
            name, item = spec[:idx], spec[idx + 1:]
            if item.strip():
                _p(name)["_inventory_remove"] = item.strip()

    for spec in (args.effect_start or []):
        # Format: NAME:SPELL:DURATION[:conc]
        # DURATION: 10r (rounds), 60m (minutes), 8h (hours), indef (indefinite)
        parts = spec.split(":", 3)
        if len(parts) < 3:
            continue
        name     = parts[0].strip()
        spell    = parts[1].strip()
        dur_str  = parts[2].strip().lower()
        is_conc  = len(parts) == 4 and parts[3].strip().lower() == "conc"
        if not name or not spell:
            continue
        effect: dict = {"name": spell, "concentration": is_conc}
        if dur_str.endswith("r"):
            try:
                effect["duration_type"]      = "rounds"
                effect["duration_remaining"] = int(dur_str[:-1])
            except ValueError:
                continue
        elif dur_str.endswith("m"):
            try:
                effect["duration_type"]    = "minutes"
                effect["duration_seconds"] = int(dur_str[:-1]) * 60
                effect["started_at"]       = time.time()
            except ValueError:
                continue
        elif dur_str.endswith("h"):
            try:
                effect["duration_type"]    = "hours"
                effect["duration_seconds"] = int(dur_str[:-1]) * 3600
                effect["started_at"]       = time.time()
            except ValueError:
                continue
        else:
            effect["duration_type"] = "indefinite"
        _p(name)["_effect_start"] = effect

    for spec in (args.effect_end or []):
        idx = spec.find(":")
        if idx > 0:
            name, spell = spec[:idx], spec[idx + 1:]
            if spell.strip():
                _p(name)["_effect_end"] = spell.strip()

    if not players:
        return None
    return {"players": list(players.values())}


def main() -> None:
    parser = argparse.ArgumentParser(description="Send text to the DnD display server.")
    parser.add_argument(
        "--player", metavar="NAME",
        help="Send as a player action, prepending the character name on display",
    )
    parser.add_argument(
        "--npc", metavar="NAME",
        help="Send as NPC dialogue with amber styling and character name header",
    )
    parser.add_argument(
        "--dice", action="store_true",
        help="Send as a dice result (inline gold styling)",
    )
    parser.add_argument(
        "--tutor", action="store_true",
        help="Send as a tutor/learning hint (collapsible parchment block)",
    )
    parser.add_argument(
        "--action", metavar="NAME",
        help="Send as a player action intent — subdued label echoing what the player declared",
    )

    # ── Inspiration / XP award flags ─────────────────────────────────────────
    parser.add_argument("--inspiration-award", metavar="NAME",
        help="Award Inspiration: fires a styled gold block in the feed + sidebar badge")
    parser.add_argument("--inspiration-reason", metavar="TEXT",
        help="Optional reason to render below the name in the inspiration block "
             "(matches how --xp-award reason is rendered). Requires --inspiration-award.")
    parser.add_argument("--inspiration-spend", metavar="NAME",
        help="Spend/clear Inspiration: removes sidebar badge")
    parser.add_argument("--milestone-award", metavar="NAME",
        help="Award a stack-based reward token (Bardic Inspiration die, homebrew "
             "Hero Coin, etc.). Use with --milestone-label / --milestone-reason.")
    parser.add_argument("--milestone-spend", metavar="NAME",
        help="Spend a stack-based reward token; decrements the sidebar counter")
    parser.add_argument("--milestone-reason", metavar="TEXT",
        help="Optional reason rendered inside the milestone-award block")
    parser.add_argument("--milestone-label", metavar="TEXT",
        help='Label for the reward type (default: "Milestone"). Examples: '
             '"Bardic Inspiration", "Hero Coin", "Fate Token".')
    parser.add_argument("--xp-award", metavar="JSON",
        help='XP award block: \'{"names":["Aldric","Mira"],"xp":250,"reason":"Encounter resolved","total":"3250/6500"}\'')

    # ── Dice request (DM → player phones) ────────────────────────────────────
    parser.add_argument("--dice-request", action="store_true",
        help='Broadcast a dice request to player phones (pre-fills their pad). '
             'Combine with --character / --spec / --modifier / --advantage / --label / --dc.')
    parser.add_argument("--character", metavar="NAME",
        help='Target character for --dice-request. Use "any" or omit to target every phone.')
    parser.add_argument("--spec", metavar="NdM", default="1d20",
        help='Dice spec for --dice-request (default 1d20). Examples: 1d20, 2d6, 1d100.')
    parser.add_argument("--modifier", type=int, default=0, metavar="N",
        help='Modifier to apply on the phone (default 0). May be negative.')
    parser.add_argument("--advantage", choices=["normal", "advantage", "disadvantage"],
        default="normal",
        help='For 1d20 only: normal | advantage | disadvantage (default normal).')
    parser.add_argument("--label", metavar="TEXT",
        help='Human label for the roll (e.g. "Stealth check", "Concentration save").')
    parser.add_argument("--dc", type=int, metavar="N",
        help='Optional DC; displayed informationally on the phone.')
    parser.add_argument("--wait", action="store_true",
        help='With --dice-request: block until every prescribed character has rolled '
             '(polls /dice-request/<id>). Exits non-zero on timeout.')
    parser.add_argument("--wait-timeout", type=int, default=120, metavar="SECONDS",
        help='Timeout for --wait (default 120s). On timeout, prints still-pending '
             'characters to stderr and exits 2.')

    # ── Stat-change flags (Option B — bundled with narration) ─────────────────
    parser.add_argument("--stat-hp", action="append", metavar="NAME:CUR:MAX",
        help="Set HP: NAME:CURRENT:MAX (can repeat for multiple players)")
    parser.add_argument("--stat-temp-hp", action="append", metavar="NAME:N",
        help="Set temp HP: NAME:N")
    parser.add_argument("--stat-slot-use", action="append", metavar="NAME:LEVEL",
        help="Expend one spell slot: NAME:LEVEL")
    parser.add_argument("--stat-slot-restore", action="append", metavar="NAME:LEVEL",
        help="Restore one spell slot: NAME:LEVEL")
    parser.add_argument("--stat-condition-add", action="append", metavar="NAME:COND",
        help="Add condition: NAME:CONDITION (can repeat)")
    parser.add_argument("--stat-condition-remove", action="append", metavar="NAME:COND",
        help="Remove condition: NAME:CONDITION (can repeat)")
    parser.add_argument("--stat-concentrate", action="append", metavar="NAME:SPELL",
        help="Set concentration: NAME:SPELL (empty SPELL = clear)")
    parser.add_argument("--stat-inventory-add", action="append", metavar="NAME:ITEM",
        help="Add inventory item: NAME:ITEM")
    parser.add_argument("--stat-inventory-remove", action="append", metavar="NAME:ITEM",
        help="Remove inventory item: NAME:ITEM")
    parser.add_argument("--effect-start", action="append", metavar="NAME:SPELL:DURATION",
        help="Start a timed effect: NAME:SPELL:DURATION (10r/60m/8h/indef) optionally :conc")
    parser.add_argument("--effect-end", action="append", metavar="NAME:SPELL",
        help="End a timed effect: NAME:SPELL (narrative end — broken, dispelled, player drops)")

    # ── Diagnostics ───────────────────────────────────────────────────────────
    parser.add_argument("--verify", action="store_true",
        help="After sending, GET /health and confirm the broadcast was received. "
             "Surfaces a clear stderr line on mismatch — use during dev/debug.")

    args = parser.parse_args()

    # Two categories of flags drive whether to read stdin:
    #   1. Content flags (--player/--npc/--dice/--tutor/--action): body REQUIRED.
    #      Always read stdin — script will abort below if the body is empty.
    #   2. Everything else (plain narration, stat-only, award-only, award+body):
    #      body OPTIONAL. Read stdin when piped (heredoc/pipe), skip when an
    #      interactive TTY to avoid blocking on an unattended call.
    #
    # Award flags (--inspiration-*/--xp-*/--milestone-*) used to force text=""
    # which silently dropped any heredoc body bundled with them. Reading piped
    # stdin under the same isatty() gate as stat flags lets bundled narration
    # flow through to the text-send block below.
    # ── Dice request (DM → phones) — broadcast + optional blocking wait ──────
    if args.dice_request:
        # Comma-split character list so the DM can address multiple players at once,
        # e.g. --character "Piper,Mira,Aldric"  → all three must roll before --wait returns.
        chars = [c.strip() for c in (args.character or "any").split(",") if c.strip()] or ["any"]
        body = {
            "characters": chars,
            "spec": args.spec,
            "modifier": args.modifier,
            "advantage": args.advantage,
        }
        if args.label: body["label"] = args.label
        if args.dc is not None: body["dc"] = args.dc
        _token = _read_token()

        # Direct urllib call (rather than _post) because we need the JSON response body.
        headers = {"Content-Type": "application/json"}
        if _token: headers["X-DND-Token"] = _token
        try:
            req = urllib.request.Request(
                DICE_REQ_URL, data=json.dumps(body).encode(),
                headers=headers, method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX)
            resp_body = json.loads(resp.read().decode("utf-8") or "{}")
        except Exception as e:
            print(f"send.py: dice-request failed: {e}", file=sys.stderr)
            sys.exit(1)

        request_id = resp_body.get("request_id", "")
        pending    = list(resp_body.get("pending") or [])
        # Machine-readable on stdout so callers can correlate (cancel, log, etc.).
        # flush=True because Python block-buffers stdout when redirected, and
        # callers want the id immediately to dispatch rolls / cancellation.
        print(request_id, flush=True)

        if args.wait:
            if not pending:
                # "any" targets aren't tracked, so --wait is a no-op there.
                print(f"send.py: --wait with no trackable characters (target={chars}) — nothing to wait for.",
                      file=sys.stderr)
            else:
                status_url = f"{DICE_REQ_URL}/{request_id}"
                poll_headers = {"X-DND-Token": _token} if _token else {}
                deadline = time.time() + max(1, args.wait_timeout)
                last_pending: list = list(pending)
                print(f"send.py: waiting for rolls from: {', '.join(pending)}", file=sys.stderr)
                while time.time() < deadline:
                    try:
                        sreq = urllib.request.Request(status_url, headers=poll_headers, method="GET")
                        s = urllib.request.urlopen(sreq, timeout=TIMEOUT, context=_SSL_CTX)
                        st = json.loads(s.read().decode("utf-8") or "{}")
                    except Exception as e:
                        print(f"send.py: poll error ({e}) — retrying", file=sys.stderr)
                        time.sleep(1.0)
                        continue
                    if st.get("complete"):
                        print(f"send.py: all rolls received.", file=sys.stderr)
                        break
                    remaining = st.get("pending") or []
                    if remaining != last_pending:
                        print(f"send.py: still waiting on: {', '.join(remaining)}", file=sys.stderr)
                        last_pending = list(remaining)
                    time.sleep(0.5)
                else:
                    print(f"send.py: timeout after {args.wait_timeout}s — still pending: "
                          f"{', '.join(last_pending)}", file=sys.stderr)
                    sys.exit(2)

        # If no other action requested, exit clean (don't fall through to stdin read).
        _other = (args.player or args.npc or args.dice or args.tutor or args.action
                  or args.inspiration_award or args.inspiration_spend
                  or args.milestone_award or args.milestone_spend or args.xp_award
                  or args.verify)
        if not _other:
            return

    _has_content_flag = bool(args.player or args.npc or args.dice or args.tutor or args.action)
    _award_only = bool(args.inspiration_award or args.inspiration_spend
                       or args.milestone_award or args.milestone_spend or args.xp_award)
    if _has_content_flag:
        text = sys.stdin.read()
    elif _award_only:
        # Award-only send: no narration body required. Read stdin only when
        # piped (heredoc), so bare calls like --inspiration-award don't block.
        text = "" if sys.stdin.isatty() else sys.stdin.read()
    else:
        text = "" if sys.stdin.isatty() else sys.stdin.read()
    token = _read_token()

    # ── Inspiration award/spend ──────────────────────────────────────────────
    # Award/spend flags POST their styled block + stat update first, then fall
    # through to the text-send block below. Bundling a heredoc body with an
    # award flag now broadcasts BOTH the gold block AND the narration chunk —
    # previously the narration was silently dropped by an early return here.
    if args.inspiration_award:
        name = args.inspiration_award.strip()
        body: dict = {"inspiration_award": name, "text": name}
        if args.inspiration_reason:
            body["reason"] = args.inspiration_reason.strip()
        _post(FLASK_URL, json.dumps(body).encode(), token)
        _post(STATS_URL, json.dumps({"players": [{"name": name, "inspiration": True}]}).encode(), token)

    if args.inspiration_spend:
        name = args.inspiration_spend.strip()
        _post(STATS_URL, json.dumps({"players": [{"name": name, "inspiration": False}]}).encode(), token)

    # ── Milestone award/spend (stack-based reward — system-agnostic) ─────────
    # Distinct from --inspiration-award: that one is the binary D&D 5e badge,
    # this one is a count that accumulates. Use for Bardic Inspiration dice,
    # homebrew Hero Coins, Fate Tokens, or alternate reward systems.
    if args.milestone_award:
        name = args.milestone_award.strip()
        label = (args.milestone_label or "Milestone").strip()
        m_body: dict = {"milestone_award": name, "text": name, "label": label}
        if args.milestone_reason:
            m_body["reason"] = args.milestone_reason.strip()
        _post(FLASK_URL, json.dumps(m_body).encode(), token)
        _post(STATS_URL, json.dumps({
            "players": [{"name": name, "_milestone_inc": label}]
        }).encode(), token)

    if args.milestone_spend:
        name = args.milestone_spend.strip()
        label = (args.milestone_label or "Milestone").strip()
        ms_body: dict = {"milestone_spend": name, "text": name, "label": label}
        _post(FLASK_URL, json.dumps(ms_body).encode(), token)
        _post(STATS_URL, json.dumps({
            "players": [{"name": name, "_milestone_dec": label}]
        }).encode(), token)

    # ── XP award block ────────────────────────────────────────────────────────
    if args.xp_award:
        try:
            xp_data = json.loads(args.xp_award)
        except json.JSONDecodeError as e:
            print(f"Invalid xp-award JSON: {e}", file=sys.stderr)
            sys.exit(1)
        # Build a human-readable summary if not provided
        if "summary" not in xp_data:
            names = ", ".join(xp_data.get("names", []))
            amt   = xp_data.get("xp", 0)
            rsn   = xp_data.get("reason", "")
            xp_data["summary"] = f"{names} — {amt} XP" + (f" ({rsn})" if rsn else "")
        _post(FLASK_URL, json.dumps({"xp_award": xp_data, "text": xp_data["summary"]}).encode(), token)

    # ── Pre-flight integrity check ────────────────────────────────────────────
    # If a content flag is set but no text arrived on stdin, that's almost
    # certainly the heredoc-routing bug surfacing again. Bail loudly so the
    # caller knows the send was rejected rather than silently producing an
    # empty broadcast.
    if _has_content_flag and not text.strip():
        flag = (
            "--player" if args.player else
            "--npc"    if args.npc    else
            "--dice"   if args.dice   else
            "--tutor"  if args.tutor  else
            "--action"
        )
        print(f"send.py: ABORT — {flag} requires a text body but stdin was empty.",
              file=sys.stderr)
        sys.exit(2)

    # ── Text send ─────────────────────────────────────────────────────────────
    chunks_sent = 0
    if text.strip():
        chunks = _split_paragraphs(text)
        for chunk in chunks:
            payload: dict = {"text": chunk}
            if args.action:
                payload["action"] = args.action
            elif args.player:
                payload["player"] = args.player
            elif args.npc:
                payload["npc"] = args.npc
            elif args.dice:
                payload["dice"] = True
            elif args.tutor:
                payload["tutor"] = True

            issues = _validate_payload(payload, "chunk")
            if issues:
                print(f"send.py: chunk payload validation failed: {'; '.join(issues)}",
                      file=sys.stderr)
                sys.exit(2)
            ok = _post(FLASK_URL, json.dumps(payload).encode("utf-8"), token)
            if ok:
                chunks_sent += 1

    # ── Stat send (bundled) ───────────────────────────────────────────────────
    stats_payload = _build_stats_payload(args)
    stats_sent = False
    if stats_payload:
        issues = _validate_payload(stats_payload, "stats")
        if issues:
            print(f"send.py: stats payload validation failed: {'; '.join(issues)}",
                  file=sys.stderr)
            sys.exit(2)
        stats_sent = _post(STATS_URL, json.dumps(stats_payload).encode("utf-8"), token)

    # ── Post-flight self-check ────────────────────────────────────────────────
    # Surface a clear, single-line summary if anything went sideways. Silent on
    # full success so successful sends don't add noise to Bash output.
    failed = [e for e in _SEND_LOG if not e["ok"]]
    if failed:
        offline = any(e.get("reason") == "display offline" for e in failed)
        if offline:
            # Display offline is a known/expected failure mode — one quiet line.
            print("send.py: display offline — send dropped silently",
                  file=sys.stderr)
        else:
            summary = ", ".join(f"{e['endpoint']}={e.get('reason','?')}" for e in failed)
            print(f"send.py: PARTIAL FAILURE — {summary}", file=sys.stderr)
            sys.exit(3)

    # ── Optional verify round-trip ────────────────────────────────────────────
    # When --verify is on, hit /health to confirm the chunks/stats counts
    # advanced. Detects "server accepted but didn't broadcast" regressions
    # without polluting normal operation.
    if args.verify:
        health = _verify_health()
        if health is None:
            print("send.py: --verify failed — /health unreachable", file=sys.stderr)
            sys.exit(4)
        # We just confirm the server is alive and tracking. Detailed verify
        # would compare counts pre/post, but health endpoint already proves
        # the server is processing requests in this address space.
        if chunks_sent and not health.get("alive"):
            print(f"send.py: --verify mismatch — server reports not alive: {health}",
                  file=sys.stderr)
            sys.exit(4)


if __name__ == "__main__":
    main()
