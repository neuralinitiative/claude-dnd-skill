"""
app.py — DnD DM display server

Receives text chunks from wrapper.py, detects scene context from keywords,
and pushes both to the browser via Server-Sent Events.

Endpoints:
    GET  /                   → serves index.html
    POST /chunk              → receives text chunk from wrapper.py
    POST /stats              → receives character/combat stat updates (merged, persisted)
    GET  /stream             → SSE stream to browser (text + scene + stats events)
    GET  /ping               → health check
    POST /clear              → wipe text log and broadcast clear event
    POST /player-input         → legacy queue endpoint (check_input.py compat)
    POST /player-input/drain   → drain legacy queue (check_input.py compat)
    POST /player-input/stage   → stage an action for review before firing
    POST /player-input/ready   → mark a staged action as ready
    POST /player-input/unstage → remove a staged action
    POST /player-input/skip    → skip a character's turn (stages + readies a skip entry)
    GET  /srd-lookup           → look up a spell/item/feature/condition by name
"""

import hmac
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
from collections import deque
from typing import Optional
from flask import Flask, Response, request, render_template, jsonify, send_from_directory
from flask_cors import CORS

# This file lives at <code-root>/display/ — resolve dirs from its location so
# paths work in any install mode (plugin, standalone skill, or dev clone).
# Writable runtime state goes to rt() (the update-safe runtime dir), NOT here.
_HERE         = os.path.dirname(os.path.abspath(__file__))
_ROOT         = os.path.dirname(_HERE)
SCRIPTS_DIR   = os.path.join(_ROOT, "scripts")
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt          # resolves <data-root>/.runtime
LOG_FILE      = rt("text_log.json")

# SRD lookup module — degrades silently if dataset not built
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
try:
    import lookup as _lookup
    _SRD_AVAILABLE = True
except Exception:
    _lookup = None          # type: ignore
    _SRD_AVAILABLE = False

from paths import find_campaign as _find_campaign

# Audio module — degrades silently if numpy not installed
_AUDIO_DIR = os.path.dirname(os.path.abspath(__file__))
import sys as _sys
if _AUDIO_DIR not in _sys.path:
    _sys.path.insert(0, _AUDIO_DIR)
try:
    import audio as _audio
    _audio.init()
except Exception:
    _audio = None   # type: ignore

# TTS module — degrades silently if Gemini API key not configured
try:
    import tts as _tts
except Exception:
    _tts = None   # type: ignore


def _apply_campaign_sfx_languages() -> None:
    """Read sfx_languages from the active campaign's state.md Session Flags.

    state.md line shape:  `sfx_languages: en,zh,es`
    Takes precedence over the DND_SFX_LANGUAGES env var when present; both
    fall back to English-only if neither is set.
    """
    if _audio is None:
        return
    try:
        camp = open(rt(".campaign"), encoding="utf-8").read().strip()
        if not camp:
            return
        state_md = _find_campaign(camp) / "state.md"
        if not state_md.exists():
            return
        text = state_md.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return
    m = re.search(r"^\s*sfx_languages:\s*([\w,\s\-]+)$", text, re.MULTILINE)
    if not m:
        return
    langs = [l.strip() for l in m.group(1).split(",") if l.strip()]
    valid = [l for l in langs if l in _audio.available_languages()]
    if valid:
        _audio.set_sfx_languages(valid)


_apply_campaign_sfx_languages()

HELP_LOCK     = rt(".help-lock")
CAMP_FILE     = rt(".campaign")
STATS_FILE    = rt("stats.json")
TOKEN_FILE    = rt(".token")
INPUT_FILE    = rt("player_input.json")
TRIGGER_FILE  = rt(".input_trigger")
QUEUE_FILE    = rt(".input_queue")
DEVICES_FILE         = rt(".approved_devices.json")
PENDING_DEVICES_FILE = rt(".pending_devices.json")

# ─── LAN / TLS mode ───────────────────────────────────────────────────────────
# Pass --lan to bind on 0.0.0.0 and protect write endpoints with a token.
# Pass --tls (requires --lan) to enable HTTPS with a self-signed cert.
# Without --lan the server binds to localhost only; no token is required.

_LAN_MODE: bool = "--lan" in sys.argv
_TLS_MODE: bool = "--tls" in sys.argv
if _LAN_MODE:
    sys.argv.remove("--lan")   # prevent Flask from seeing an unknown flag
if _TLS_MODE:
    sys.argv.remove("--tls")


def _get_or_create_token() -> str:
    """Load or generate the LAN token. Upgrades short legacy tokens to 64-char."""
    try:
        token = open(TOKEN_FILE, encoding="utf-8").read().strip()
        if len(token) >= 48:   # 48+ chars = already long enough
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_hex(32)   # 64-char hex — brute force infeasible
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    os.chmod(TOKEN_FILE, 0o600)
    return token


_lan_token: Optional[str] = _get_or_create_token() if _LAN_MODE else None


# ─── Rate limiting ────────────────────────────────────────────────────────────
# Simple in-process sliding window: max 20 write requests per IP per minute.
# Prevents spam injection and brute-force token guessing on write endpoints.

import time as _time

_rate_buckets: dict[str, list] = {}
_rate_lock = threading.Lock()
_RATE_WINDOW = 60    # seconds
_RATE_MAX    = 20    # requests per window per IP


def _rate_ok(ip: str) -> bool:
    now = _time.time()
    with _rate_lock:
        bucket = [t for t in _rate_buckets.get(ip, []) if now - t < _RATE_WINDOW]
        if len(bucket) >= _RATE_MAX:
            return False
        bucket.append(now)
        _rate_buckets[ip] = bucket
    return True


# ─── Input validation helpers ─────────────────────────────────────────────────

_PRINTABLE    = re.compile(
    "[^"
    "\x20-\x7E"                 # ASCII printable
    " -ɏ"             # Latin-1 + Latin Extended A/B (é ñ ö ć ş ž etc.)
    "Ͱ-Ͽ"             # Greek
    "Ѐ-ӿ"             # Cyrillic (Russian, Ukrainian)
    "֐-׿"             # Hebrew
    "؀-ۿ"             # Arabic
    "ݐ-ݿ"             # Arabic Supplement
    "ऀ-ॿ"             # Devanagari (Hindi, Marathi)
    "ঀ-৿"             # Bengali
    "஀-௿"             # Tamil
    "ఀ-౿"             # Telugu
    "฀-๿"             # Thai
    "Ḁ-ỿ"             # Latin Extended Additional (Vietnamese diacritics)
    "　-〿"             # CJK Symbols and Punctuation
    "぀-ゟ"             # Hiragana
    "゠-ヿ"             # Katakana
    "㐀-䶿"             # CJK Extension A
    "一-鿿"             # CJK Unified Ideographs
    "가-힯"             # Hangul Syllables
    "＀-￯"             # Halfwidth / Fullwidth
    "]"
)
_SHELL_CHARS  = re.compile(r'[$`\\;|&><()\[\]{}!]')
# Unicode \w covers letters from all scripts above. Allow space, apostrophe, hyphen
# inside the name; trim 1-2 char names to a separate branch.
_CHAR_NAME_RE = re.compile(r"^\w[\w '\-]{0,48}\w$|^\w{1,2}$", re.UNICODE)


def _sanitize_input(text: str) -> str:
    """Strip control chars and shell metacharacters from player input text."""
    text = _SHELL_CHARS.sub("", text)
    text = _PRINTABLE.sub("", text)
    return text[:500].strip()


def _char_ok(name: str, known: set) -> bool:
    """Return True if character name is syntactically valid and in the party."""
    if not _CHAR_NAME_RE.match(name):
        return False
    if known and name not in known and name != "Everybody":
        return False
    return True


# ─── Device approval system ───────────────────────────────────────────────────
# Each browser generates a UUID device ID (localStorage). On first input attempt
# from an unseen LAN device, the request is held and the DM sees an Approve/Deny
# card on the display. Localhost is auto-approved. Denied devices are blocked for
# the session.

_approved_devices: set[str]       = set()
_denied_devices:   set[str]       = set()
_pending_devices:  dict[str, dict] = {}  # device_id -> {ip, first_seen}
_devices_lock = threading.Lock()


def _persist_approved_devices() -> None:
    """Persist approved devices to disk. Must be called WITHOUT _devices_lock held."""
    try:
        with _devices_lock:
            data = list(_approved_devices)
        with open(DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(DEVICES_FILE, 0o600)
    except Exception:
        pass


def _load_approved_devices() -> None:
    try:
        with open(DEVICES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with _devices_lock:
            for d in data:
                _approved_devices.add(str(d))
    except Exception:
        pass


def _persist_pending_devices() -> None:
    """Persist pending devices to disk so they survive app restarts. Must be called WITHOUT _devices_lock held."""
    try:
        with _devices_lock:
            data = list(_pending_devices.values())
        with open(PENDING_DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(PENDING_DEVICES_FILE, 0o600)
    except Exception:
        pass


def _load_pending_devices() -> None:
    try:
        with open(PENDING_DEVICES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with _devices_lock:
            for d in data:
                if isinstance(d, dict) and d.get("id"):
                    # Skip if already approved/denied during this run
                    if d["id"] not in _approved_devices and d["id"] not in _denied_devices:
                        _pending_devices[d["id"]] = d
    except Exception:
        pass


_load_approved_devices()
_load_pending_devices()


# A casual home-LAN game doesn't need a per-device approval gate — it's friction
# (every phone sits on "Awaiting approval" until the DM taps a card). Default:
# trust any device that can already reach the server. Set DND_REQUIRE_APPROVAL=1
# to restore the approve/deny gate (e.g. on an untrusted/shared network).
_REQUIRE_APPROVAL = os.environ.get("DND_REQUIRE_APPROVAL", "").strip().lower() in ("1", "true", "yes", "on")


def _device_ok(device_id: str, ip: str) -> str:
    """Return 'approved', 'pending', or 'denied' for a given device."""
    if not device_id:
        return "denied"
    _need_persist_approved = False
    _need_persist_pending  = False
    with _devices_lock:
        if device_id in _approved_devices:
            return "approved"
        if device_id in _denied_devices:
            return "denied"
        # Auto-approve localhost always, and every reachable device unless the
        # approval gate is explicitly required.
        if not _REQUIRE_APPROVAL or ip in ("127.0.0.1", "::1"):
            _approved_devices.add(device_id)
            _need_persist_approved = True
        # New LAN device with the gate on — hold and notify DM
        elif device_id not in _pending_devices:
            _pending_devices[device_id] = {
                "id":         device_id,
                "ip":         ip,
                "first_seen": _time.time(),
            }
            _need_persist_pending = True
            _broadcast({"device_request": {"id": device_id, "ip": ip}})
    # Persist outside the lock to avoid deadlock (Lock is not reentrant)
    if _need_persist_approved:
        _persist_approved_devices()
        return "approved"
    if _need_persist_pending:
        _persist_pending_devices()
    return "pending"


# ─── Staged input system ──────────────────────────────────────────────────────
# Players stage their actions from the display companion UI. When all expected
# players mark ready, the combined action is written to TRIGGER_FILE for
# wrapper.py to inject into Claude's PTY stdin.

_staged: dict[str, dict] = {}   # {char_name: {text, ready, timestamp}}
_staged_lock = threading.Lock()
_expected_count = 1             # updated when stats arrive; min 1
_autorun_threshold: Optional[int] = None  # overrides _expected_count when set via push_stats --autorun-threshold

# Tracks which character names are currently sitting in .input_queue waiting
# for the DM to press Enter. Set when queue is written, cleared when wrapper
# POSTs /queue/consumed after injection. Persists through page reloads via SSE
# initial data and is broadcast to all connected clients on change.
_queue_status: list = []
_queue_status_lock = threading.Lock()

# Last autorun cycle broadcast — replayed on SSE reconnect so late-joining
# clients start the countdown from the correct elapsed position.
# Cleared when autorun_waiting=false (turn resolved or autorun disabled).
_autorun_cycle: Optional[dict] = None
_autorun_cycle_lock = threading.Lock()


def _normalize_slot(slot: dict) -> None:
    """Coerce a spell-slot entry to the canonical {used, max} shape in place.

    Tolerates legacy/alt payloads that use `remaining` instead of `used`.
    Without this, _slot_use/_slot_restore raise KeyError on a slot stored
    under the alt schema (e.g. after a long-rest --spell-slots full-replace).
    """
    if "used" in slot:
        return
    mx = slot.get("max", 0)
    if "remaining" in slot:
        slot["used"] = max(mx - int(slot.get("remaining", 0)), 0)
    else:
        slot["used"] = 0


def _staged_snapshot() -> dict:
    """Return a serialisable copy of the staged dict (no IP field)."""
    return {k: {"text": v["text"], "ready": v["ready"]} for k, v in _staged.items()}


def _check_auto_trigger() -> None:
    """Move staged-and-ready actions into the DM-gated queue file (.input_queue).

    .input_queue is NOT injected immediately — wrapper.py picks it up the next
    time the DM presses Enter (or Claude explicitly triggers via .input_trigger).
    This gives the DM control over when player actions enter Claude's context.
    """
    with _staged_lock:
        if not _staged:
            return
        everybody_ready = "Everybody" in _staged and _staged["Everybody"]["ready"]
        all_ready       = all(v["ready"] for v in _staged.values())
        threshold       = _autorun_threshold if _autorun_threshold is not None else _expected_count
        enough          = len(_staged) >= threshold or everybody_ready
        if not (all_ready and enough):
            return
        char_names = list(_staged.keys())
        lines      = [f'[{c}]: {e["text"]}' for c, e in _staged.items()]
        content    = "\n".join(lines)
        _staged.clear()

    try:
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        char_names = []

    if char_names:
        with _queue_status_lock:
            _queue_status.clear()
            _queue_status.extend(char_names)
    _broadcast({"staged_inputs": {}, "queue_status": list(char_names)})


def _token_ok() -> bool:
    """Return True if the request carries the correct LAN token (or we're in localhost mode)."""
    if _lan_token is None:
        return True   # localhost mode — no token required
    provided = request.headers.get("X-DND-Token", "")
    return hmac.compare_digest(provided, _lan_token)


app = Flask(__name__)

app.config['TEMPLATES_AUTO_RELOAD'] = True
CORS(app)

# Wire audio broadcast after _broadcast is defined (see bottom of file)
# — done lazily via set_broadcast() called after app is created.

# ─── Scene definitions ────────────────────────────────────────────────────────
# Each scene: keywords (weighted — more = higher priority hit),
# gradient colors [top, bottom], accent color, particle type, display label.

SCENES: dict[str, dict] = {
    "tavern": {
        "keywords": [
            "tavern", "inn", "guttered", "common room", "hearth",
            "fireplace", "ale", "mead", "barkeep", "innkeeper",
            "candle", "tallow", "flagon", "stool", "bar",
        ],
        "colors": ["#1a0800", "#2e1400"],
        "accent": "#c8601a",
        "particles": "embers",
        "label": "The Inn",
    },
    "dungeon": {
        "keywords": [
            "dungeon", "corridor", "stone floor", "torch", "iron gate",
            "portcullis", "cell", "shackle", "pit", "dank",
        ],
        "colors": ["#080818", "#12082e"],
        "accent": "#6a3aaa",
        "particles": "dust",
        "label": "The Dungeon",
    },
    "mine": {
        "keywords": [
            "mine", "seam", "shaft", "tunnel", "ore", "pickaxe",
            "foreman", "deep seam", "ashstone", "cart", "vein",
        ],
        "colors": ["#0a0a0a", "#1a1008"],
        "accent": "#806040",
        "particles": "dust",
        "label": "The Mine",
    },
    "cave": {
        "keywords": [
            "cave", "cavern", "stalactite", "stalagmite", "underground",
            "grotto", "dripping", "echo", "subterranean",
        ],
        "colors": ["#0a1520", "#0a1030"],
        "accent": "#2060a0",
        "particles": "mist",
        "label": "The Cavern",
    },
    "forest": {
        "keywords": [
            "forest", "wood", "tree", "branch", "leaves", "undergrowth",
            "hollow wood", "canopy", "root", "bark", "moss", "fern",
            "thicket", "grove",
        ],
        "colors": ["#041008", "#081a04"],
        "accent": "#40a040",
        "particles": "leaves",
        "label": "The Forest",
    },
    "castle": {
        "keywords": [
            "castle", "rampart", "battlement", "keep", "parapet",
            "drawbridge", "moat", "throne", "great hall", "manor",
        ],
        "colors": ["#0e0e1a", "#1a1a2e"],
        "accent": "#8080c0",
        "particles": "dust",
        "label": "The Castle",
    },
    "mountain": {
        "keywords": [
            "mountain", "snow", "peak", "blizzard", "frost", "glacier",
            "avalanche", "ridge", "cliff", "altitude", "wind",
        ],
        "colors": ["#0a1020", "#1a2040"],
        "accent": "#a0c0e0",
        "particles": "snow",
        "label": "The Mountains",
    },
    "ocean": {
        "keywords": [
            "ocean", "sea", "ship", "wave", "sailor", "port", "harbour",
            "dock", "tide", "storm", "mast", "hull", "water",
        ],
        "colors": ["#000d1a", "#001a33"],
        "accent": "#0060a0",
        "particles": "ripples",
        "label": "The Sea",
    },
    "desert": {
        "keywords": [
            "desert", "sand", "dune", "oasis", "scorching", "arid",
            "mirage", "camel", "sphinx",
        ],
        "colors": ["#1a0f00", "#2e1a00"],
        "accent": "#c08030",
        "particles": "sand",
        "label": "The Desert",
    },
    "ruins": {
        "keywords": [
            "ruins", "ruin", "crumble", "crumbling", "rubble", "ancient",
            "overgrown", "collapsed", "forgotten", "desolate", "remnant",
        ],
        "colors": ["#100e04", "#1e1a08"],
        "accent": "#806830",
        "particles": "dust",
        "label": "The Ruins",
    },
    "swamp": {
        "keywords": [
            "swamp", "marsh", "bog", "mud", "murky", "fetid", "reed",
            "mire", "sludge", "stagnant",
        ],
        "colors": ["#080e04", "#0e1808"],
        "accent": "#406020",
        "particles": "mist",
        "label": "The Swamp",
    },
    "crypt": {
        "keywords": [
            "crypt", "tomb", "grave", "coffin", "undead", "bones",
            "skeleton", "lich", "mausoleum", "burial", "sarcophagus",
            "dead", "death",
        ],
        "colors": ["#08000a", "#140014"],
        "accent": "#602060",
        "particles": "smoke",
        "label": "The Crypt",
    },
    "fire": {
        "keywords": [
            "fire", "flame", "burn", "blaze", "inferno", "conflagration",
            "ember", "char", "smoke", "ash cloud",
        ],
        "colors": ["#1a0500", "#2e0800"],
        "accent": "#ff4400",
        "particles": "embers",
        "label": "The Fire",
    },
    "arcane": {
        "keywords": [
            "arcane", "magic", "spell", "enchant", "rune", "glyph",
            "mystical", "ritual", "incantation", "ward", "sigil",
            "thaumaturgy", "sorcery",
        ],
        "colors": ["#080020", "#12003a"],
        "accent": "#8040ff",
        "particles": "sparks",
        "label": "The Arcane",
    },
    "city": {
        "keywords": [
            "city", "market", "street", "crowd", "village", "town",
            "square", "cobble", "district", "quarter", "merchant",
            "ashenveil",
        ],
        "colors": ["#0a0f1a", "#15202e"],
        "accent": "#6080a0",
        "particles": "rain",
        "label": "The Town",
    },
    "night": {
        "keywords": [
            "night", "midnight", "moon", "star", "dark sky",
            "constellation", "celestial", "dusk", "twilight",
        ],
        "colors": ["#000008", "#04000f"],
        "accent": "#4060a0",
        "particles": "stars",
        "label": "The Night",
    },
    "temple": {
        "keywords": [
            "temple", "shrine", "altar", "holy", "sacred", "chapel",
            "prayer", "cleric", "incense", "lantern", "pew", "nave",
            "pale flame",
        ],
        "colors": ["#0e0c18", "#1a1428"],
        "accent": "#c0a060",
        "particles": "smoke",
        "label": "The Temple",
    },
}

# Priority order — checked in sequence; first match wins per chunk
SCENE_PRIORITY = [
    "mine", "crypt", "arcane", "fire", "temple", "dungeon", "cave",
    "forest", "swamp", "castle", "ocean", "mountain", "desert", "ruins",
    "tavern", "city", "night",
]

# ─── ANSI / TUI chrome stripping ─────────────────────────────────────────────

class _ANSIState:
    """Character-by-character ANSI escape-sequence state machine.

    Regex approaches fail when the PTY delivers bytes one at a time, splitting
    sequences like \\x1b[4;2m across chunk boundaries.  This state machine
    carries its state across calls so cross-chunk splits are handled correctly.

    States
    ------
    normal   → emitting regular characters
    esc      → saw ESC (0x1B), waiting to see what kind of sequence follows
    csi      → inside CSI sequence (ESC [ … letter)
    osc      → inside OSC sequence (ESC ] … BEL or ST)
    osc_esc  → inside OSC, just saw ESC — might be the ST terminator (ESC \\)
    """

    __slots__ = ("_s",)

    def __init__(self) -> None:
        self._s: str = "normal"

    def feed(self, text: str) -> str:
        out: list[str] = []
        s = self._s
        for ch in text:
            c = ord(ch)
            if s == "normal":
                if c == 0x1B:
                    s = "esc"
                elif c >= 0x20 or c in (0x09, 0x0A):   # printable / tab / newline
                    out.append(ch)
                # else: other control char (bell, etc.) — discard
            elif s == "esc":
                if ch == "[":
                    s = "csi"
                elif ch == "]":
                    s = "osc"
                else:
                    s = "normal"    # 2-char ESC sequence; discard both bytes
            elif s == "csi":
                if 0x40 <= c <= 0x7E:   # final byte of CSI
                    s = "normal"
                elif c == 0x1B:         # unexpected ESC — start fresh
                    s = "esc"
                # else: parameter / intermediate byte, keep consuming
            elif s == "osc":
                if c == 0x07:           # BEL terminates OSC
                    s = "normal"
                elif c == 0x1B:
                    s = "osc_esc"
                # else: OSC payload, keep consuming
            elif s == "osc_esc":
                s = "normal" if ch == "\\" else "osc"
        self._s = s
        return "".join(out)


_ansi = _ANSIState()
_ansi_lock = threading.Lock()

_BOX_CHARS = set("╭╮╰╯│─┌┐└┘├┤┬┴┼━═║╔╗╚╝")
_BOX_CHAR_STRIP = "╭╮╰╯│─┌┐└┘├┤┬┴┼━═║╔╗╚╝"  # same set as string for str.strip()

# Characters used by Claude CLI spinner / prompt / UI
_SPINNER_CHARS = set("✽⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏◐◓◑◒◌◎●")
_PROMPT_STARTS = ("❯", ">", "·", "▸", "ℹ", "✓", "⚠", "✗", "⟳", "↳")


def _handle_cr(text: str) -> str:
    """Handle carriage returns the way a real terminal would.

    Two distinct cases:
      \\r\\n  — a real newline (\\r\\n line ending).  Normalise to \\n first
                so the content is preserved.
      bare \\r — cursor-to-column-0 for in-place token updates.  Claude CLI
                streams each token by rewriting the current line:
                  "The" → \\r"The Gut" → \\r"The Gutte" → …
                Keep only the last segment (= the final written state).
    """
    # Step 1: treat \\r\\n as a real newline — must come before bare-\\r logic
    text = text.replace("\r\n", "\n")

    # Step 2: handle remaining bare \\r (in-place rewrites)
    lines = text.split("\n")
    result = []
    for line in lines:
        if "\r" in line:
            parts = line.split("\r")
            result.append(parts[-1])   # last segment = final state of the line
        else:
            result.append(line)
    return "\n".join(result)


def _strip_ansi(text: str) -> str:
    text = _handle_cr(text)
    with _ansi_lock:
        text = _ansi.feed(text)
    return text


def _is_chrome(line: str) -> bool:
    """Return True for lines that are TUI chrome, not DM narration.

    The Claude CLI wraps responses in a box:
        ╭──────────────────╮
        │ narration text   │
        ╰──────────────────╯
    We strip the border characters from line edges first so that content
    lines like "│ The tavern smells of ale │" are NOT filtered — only pure
    border rows (all box chars, no letters) are treated as chrome.
    """
    stripped = line.strip()

    if not stripped:
        return False   # keep blank lines — they separate paragraphs

    # Strip leading/trailing box-drawing border chars to expose the real content.
    # "│ The tavern smells of ale │" → "The tavern smells of ale"
    content = stripped.strip(_BOX_CHAR_STRIP + " ")

    # If nothing remains, the line was entirely box-drawing chrome (a border row).
    if not content:
        return True

    # All remaining checks operate on content (without box border decoration).
    c = content

    # CLI prompt / spinner lines
    if c[0] in _SPINNER_CHARS:
        return True
    if c.startswith(_PROMPT_STARTS):
        return True

    # Common spinner word patterns (e.g. "Thinking…")
    if re.match(r"^[A-Z][a-z]+ing…?$", c):
        return True

    # Claude branding / metadata
    if "claude.ai" in c.lower():
        return True

    # Session-resume instructions emitted at end of response
    if c.startswith("Resume this session with:") or re.match(r"^claude\s+--resume\s+", c):
        return True

    # Status-bar patterns: cost, token counts, rate-limit bars
    # Note: "Tokens300/0" has no space — use \s* not \s+
    if re.search(r"Tokens\s*\d|5hr:|7d:|Session:|Total:\s*\$", c):
        return True

    # Model/plan header lines ("Sonnet 4.6", "Claude Pro", "Professional", etc.)
    if re.search(r"Sonnet|Haiku|Opus|Claude\s*(Pro|Max|Team|Code)\b|Professional\b|claude-\d", c, re.I):
        return True

    # Tool-use labels emitted by Claude CLI ("Bash command", "Read command", etc.)
    if re.match(r"^(Bash|Read|Write|Edit|Glob|Grep|WebFetch|WebSearch|TaskCreate|TaskUpdate|TaskGet|TaskList|NotebookEdit|Agent|ToolSearch|ExitPlanMode|EnterPlanMode|ScheduleWakeup|Monitor|RemoteTrigger|CronCreate|CronDelete|CronList|AskUserQuestion)(\s+(command|tool|result|call))?$", c, re.I):
        return True

    # Timestamp-prefixed lines ("3ts ago …", "2m ago …") — UI timestamps concatenated with content
    if re.match(r"^\d+\s*[smhdt]+s?\s*(ago\s*)?[A-Z]", c):
        return True

    # Bare numbers (token counts, cursor column positions, etc.)
    if re.match(r"^\d+$", c):
        return True

    # Single stray characters that are ANSI/escape remnants, not real words
    if len(c) == 1 and not c.isalpha():
        return True

    # Very short non-alpha fragments (≤3 chars with no letters = not narration)
    if len(c) <= 3 and not re.search(r"[a-zA-Z]{2}", c):
        return True

    return False


def _clean(text: str) -> str:
    text = _strip_ansi(text)
    lines = text.split("\n")
    kept = []
    for line in lines:
        if _is_chrome(line):
            continue
        # Strip box-border chars from edges so content reaches the browser clean.
        s = line.strip().strip(_BOX_CHAR_STRIP + " ")
        # Blank line → preserve as paragraph separator
        kept.append(s if s else "")
    # Collapse runs of more than two consecutive blank lines
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
    return result


# ─── Scene detection ──────────────────────────────────────────────────────────

_current_scene_name: str = "tavern"   # default — we start in the inn
_scene_buffer: list[str] = []
_BUFFER_WINDOW = 20   # analyse last N cleaned chunks together


def _detect_scene(text: str) -> Optional[dict]:
    global _current_scene_name, _scene_buffer

    _scene_buffer.append(text.lower())
    if len(_scene_buffer) > _BUFFER_WINDOW:
        _scene_buffer.pop(0)

    window = " ".join(_scene_buffer)

    scores: dict[str, int] = {}
    for scene_name in SCENE_PRIORITY:
        scene = SCENES[scene_name]
        score = sum(window.count(kw) for kw in scene["keywords"])
        if score > 0:
            scores[scene_name] = score

    if not scores:
        return None

    best = max(scores, key=lambda k: scores[k])
    if best == _current_scene_name:
        return None   # no change

    _current_scene_name = best
    return SCENES[best] | {"name": best}


# ─── SSE client registry ─────────────────────────────────────────────────────

_clients: list[queue.Queue] = []
_clients_lock = threading.Lock()
# Maps a connected SSE client (queue) → the character it's bound to, if any.
# Phones connect to /stream?character=<name>; the main display has no character.
# Lets a dice-request know whether a target PC has a live phone (→ route there)
# or not (→ open the on-screen roller). Guarded by _clients_lock.
_client_chars: "dict[queue.Queue, str]" = {}


def _phone_present(char: str) -> bool:
    """True if some connected phone is bound to this character (case-insensitive)."""
    c = (char or "").strip().lower()
    if not c:
        return False
    with _clients_lock:
        return c in _client_chars.values()

# ─── Text replay log ──────────────────────────────────────────────────────────
# Stores cleaned text chunks so late-connecting browsers can catch up.
# Persisted to LOG_FILE so it survives Flask restarts (Chromecast reconnects, new sessions).
# Full body retention (player requirement, 2026-08-18): cap raised 60 → 2000 with
# full reload at startup (_load_log), so cross-session body text stays recoverable
# and archivable in both formats. On plugin updates, re-check these three spots.
_text_log: deque = deque(maxlen=2000)
_text_log_lock = threading.Lock()

# ─── Session tail buffer ──────────────────────────────────────────────────────
# Rolling buffer of the last 30 text events — written to session_tail.json after
# every /chunk POST so it survives crashes. Read at /dnd load for display replay.
# Path is campaign-specific so tails from different campaigns don't overwrite each other.
#
# ROBUSTNESS GUARANTEES (after the 2026-05-01 wipe-bug fix):
#   1. _load_tail is NON-DESTRUCTIVE: it never wipes the in-memory buffer based
#      on an empty/filtered-out load. If the file is empty, missing, or every
#      entry is filtered out by campaign mismatch, the existing buffer stays.
#   2. _persist_tail SKIPS ON EMPTY: it never overwrites an existing non-empty
#      file with an empty buffer. This breaks the "filter zeros buffer →
#      persist writes [] → file lost" failure chain.
#   3. _persist_tail uses ATOMIC WRITES: writes to a tempfile and atomically
#      renames into place, so a partial/crashed write can never produce a
#      truncated or zero-byte file.
#   4. The legacy fallback path is GONE. Tails only ever land in the campaign-
#      specific file. If CAMP_FILE is missing/empty when persist would fire,
#      we keep the buffer in memory and skip the write rather than dropping
#      events into a shared file that bleeds across campaigns.
_tail_buffer: deque = deque(maxlen=30)
_tail_lock   = threading.Lock()


def _get_tail_file() -> "str | None":
    """Return the campaign-specific tail path, or None if no campaign is set.

    Previously this fell back to a process-local path on the skill side. That
    fallback caused tail bleed across campaigns and made the wipe-on-load bug
    much harder to diagnose. The new contract: campaign-specific or nothing.
    """
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if camp:
            return str(_find_campaign(camp) / "session_tail.json")
    except Exception:
        pass
    return None


def _persist_tail() -> None:
    """Write _tail_buffer to disk. Refuses to overwrite content with empty.

    Atomic-write guarantee: writes to <path>.tmp then renames, so observers
    (the next /dnd load reading the file) never see a partial or zero-byte
    state.
    """
    path = _get_tail_file()
    if not path:
        # No active campaign — keep the buffer in memory, skip disk.
        return
    try:
        with _tail_lock:
            data = list(_tail_buffer)
        # Skip-on-empty guard: never blank a file that currently has content.
        if not data and os.path.exists(path):
            try:
                if os.path.getsize(path) > 2:  # 2 bytes = "[]"
                    print(f"_persist_tail: skipping empty write — {path} has content",
                          file=sys.stderr)
                    return
            except OSError:
                pass
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception as e:
        print(f"_persist_tail: write failed: {e}", file=sys.stderr)


def _load_tail() -> None:
    """Load tail from disk into the buffer. NON-DESTRUCTIVE on empty/mismatch.

    Old behavior: cleared the buffer first, then re-appended filtered entries.
    This created the wipe bug: if every entry was filtered out (campaign
    mismatch) the buffer ended up empty and the next persist wrote [] to disk.

    New behavior: build the candidate buffer first, then ONLY swap it into
    place if at least one entry survived filtering. If nothing survives, the
    in-memory buffer is left alone — preserves whatever was already loaded.
    """
    path = _get_tail_file()
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return  # No file yet — keep in-memory state
    except (OSError, json.JSONDecodeError) as e:
        print(f"_load_tail: read failed for {path}: {e}", file=sys.stderr)
        return
    if not isinstance(data, list):
        print(f"_load_tail: file content is not a list — leaving buffer alone",
              file=sys.stderr)
        return

    try:
        current_camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        current_camp = ""

    candidate: list = []
    for item in data[-30:]:
        if not isinstance(item, dict):
            continue
        item_camp = item.get("_camp", "")
        # If we know the campaign and the entry stamps a different campaign,
        # skip it. Entries with no stamp are kept (legacy data + tolerance).
        if current_camp and item_camp and item_camp != current_camp:
            continue
        candidate.append(item)

    if not candidate:
        # Loaded data filtered down to nothing. DO NOT replace the buffer —
        # this is the wipe-bug guard.
        return

    with _tail_lock:
        _tail_buffer.clear()
        for item in candidate:
            _tail_buffer.append(item)


_load_tail()


def _persist_log() -> None:
    """Write the current text log to disk. Called after every chunk."""
    try:
        with _text_log_lock:
            data = list(_text_log)
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _load_log() -> None:
    """Load a previously persisted text log on startup.
    Handles both old string format and new dict format."""
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with _text_log_lock:
            _text_log.clear()
            for item in data[:]:
                # Migrate old plain-string entries to dict format
                if isinstance(item, str):
                    item = {"text": item}
                _text_log.append(item)
    except Exception:
        pass


_load_log()


# ─── Character / combat stats ─────────────────────────────────────────────────
# Stored as {"players": [...], "turn_order": {...}|null}
# Players are merged by name so partial updates (just HP, just XP) work.

_current_stats: dict = {}
_stats_lock = threading.Lock()


def _persist_stats() -> None:
    try:
        with _stats_lock:
            data = dict(_current_stats)
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _load_stats() -> None:
    global _expected_count
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with _stats_lock:
            _current_stats.update(data)
        # Initialise expected player count from persisted stats so solo-mode
        # detection is correct immediately after restart, without waiting for
        # the next /stats POST.
        loaded_players = data.get("players", [])
        if loaded_players:
            _expected_count = max(1, len(loaded_players))
    except Exception:
        pass


_load_stats()


# ─── Player input queue ───────────────────────────────────────────────────────
# Stores actions submitted from the display companion (iPad etc.) until the DM
# triggers the next turn. Drained by check_input.py via /player-input/drain.

_input_queue: list[dict] = []
_input_lock = threading.Lock()

# Pending DM-issued dice requests: request_id → {chars: set[str], meta: {...}, started_at: float}
# A request is "complete" when its chars set is empty (every prescribed player rolled).
# send.py --wait polls GET /dice-request/<id> to know when the DM can move on.
_dice_pending: dict = {}
_dice_pending_lock = threading.Lock()


def _dice_pending_snapshot() -> list:
    with _dice_pending_lock:
        return [
            {"request_id": rid, "pending": sorted(e["chars"]), "label": e["meta"].get("label", "")}
            for rid, e in _dice_pending.items() if e["chars"]
        ]


def _load_input_queue() -> None:
    global _input_queue
    try:
        with open(INPUT_FILE, encoding="utf-8") as f:
            _input_queue = json.load(f)
    except Exception:
        _input_queue = []


def _persist_input_queue() -> None:
    try:
        with open(INPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(_input_queue, f)
    except Exception:
        pass


_load_input_queue()


def _broadcast(payload: dict) -> None:
    with _clients_lock:
        dead = []
        for q in _clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _clients.remove(q)
            _client_chars.pop(q, None)


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    # Pass LAN token to template so the browser can authenticate /help-request
    return render_template(
        "index.html",
        lan_token=_lan_token or "",
        narrator_voice=_read_narrator_voice(),
        tts_available=(_tts is not None),
    )


@app.route("/icons/<path:filename>")
def serve_icon(filename):
    """Serve icons, favicon, and brand assets from display/icons/."""
    _icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
    return send_from_directory(_icons_dir, filename)


@app.route("/favicon.ico")
def favicon():
    _icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
    return send_from_directory(_icons_dir, "favicon.ico",
                               mimetype="image/vnd.microsoft.icon")


@app.route("/srd-lookup")
def srd_lookup():
    """Look up a spell, item, condition, feature, or monster by name.

    Query params:
        name      — the name to look up (required)
        category  — spell | item | equipment | magic_item | condition | monster | feature (optional)
        level     — character level (1–20); collapses scale progressions to the matching entry

    Returns JSON: {"found": bool, "name": str, "category": str, "text": str}
    """
    name     = request.args.get("name", "").strip()[:120]
    category = request.args.get("category", "").strip().lower() or None
    level_s  = request.args.get("level", "").strip()
    level    = int(level_s) if level_s.isdigit() and 1 <= int(level_s) <= 20 else None
    if not name:
        return jsonify({"found": False, "error": "name required"}), 400
    if not _SRD_AVAILABLE or _lookup is None:
        return jsonify({"found": False, "error": "SRD dataset not loaded"}), 503

    text = _lookup.lookup_with_level(name, category=category, level=level)
    if text:
        rec = _lookup.lookup_record(name, category=category)
        resolved_cat = (rec or {}).get("_cat", category or "")
        return jsonify({"found": True, "name": name, "category": resolved_cat, "text": text})
    # Not found — offer near-miss "did you mean?" suggestions (typo recovery)
    # plus the wikidot fallback URL so the frontend can still link out.
    wurl = _lookup.wikidot_url(name, category=category)
    suggestions = []
    try:
        for sg_name, sg_cat in _lookup.suggest(name, category=category, n=3):
            suggestions.append({"name": sg_name, "category": sg_cat})
    except Exception:
        pass  # suggestion is best-effort; never fail the lookup over it
    return jsonify({"found": False, "name": name,
                    "wikidot_url": wurl, "suggestions": suggestions})


@app.route("/ping")
def ping():
    return "ok", 200


@app.route("/health")
def health():
    """Server-side integrity probe used by send.py --verify and external monitors.

    Returns the live counts the send-side cares about:
      - alive: always True if the route runs
      - tail_buffer: number of entries currently in the rolling tail
      - tail_file_size: size in bytes of the on-disk session_tail.json
      - text_log: number of entries in the replay log
      - campaign: the active campaign name (empty if none set)
      - clients: connected SSE clients

    No auth required — this is a liveness/monitoring endpoint, no PII or game
    content is exposed.
    """
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        camp = ""
    tail_path = _get_tail_file()
    try:
        tail_size = os.path.getsize(tail_path) if tail_path and os.path.exists(tail_path) else 0
    except OSError:
        tail_size = 0
    with _tail_lock:
        tail_count = len(_tail_buffer)
    with _text_log_lock:
        log_count = len(_text_log)
    with _clients_lock:
        client_count = len(_clients)
    return {
        "alive": True,
        "tail_buffer": tail_count,
        "tail_file_size": tail_size,
        "tail_path": tail_path or "",
        "text_log": log_count,
        "campaign": camp,
        "clients": client_count,
    }, 200


@app.route("/chunk", methods=["POST"])
def chunk():
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    raw = data.get("text", "")
    if not raw:
        return "", 204

    is_action      = bool(data.get("action"))
    is_player      = bool(data.get("player"))
    is_npc         = bool(data.get("npc"))
    is_dice        = bool(data.get("dice"))
    is_tutor       = bool(data.get("tutor"))
    is_inspiration = bool(data.get("inspiration_award"))
    is_milestone_award = bool(data.get("milestone_award"))
    is_milestone_spend = bool(data.get("milestone_spend"))
    is_xp_award    = bool(data.get("xp_award"))

    # ── Milestone award/spend (stack-based reward, distinct from binary Inspiration) ──
    if is_milestone_award or is_milestone_spend:
        name = str(data.get("milestone_award") or data.get("milestone_spend") or "").strip()[:80]
        label = str(data.get("label") or "Milestone").strip()[:40]
        if not name:
            return "", 204
        payload: dict = {
            "milestone_award" if is_milestone_award else "milestone_spend": name,
            "label": label,
            "text": name,
        }
        log_entry: dict = dict(payload)
        if is_milestone_award and data.get("reason"):
            payload["reason"] = str(data["reason"]).strip()[:240]
            log_entry["reason"] = payload["reason"]
        with _text_log_lock:
            _text_log.append(log_entry)
        with _tail_lock:
            _tail_buffer.append(log_entry)
        _persist_log()
        _persist_tail()
        _broadcast(payload)
        return "", 204

    # Inspiration and XP awards carry no text from stdin — build synthetic text.
    if is_inspiration:
        name = str(data.get("inspiration_award", "")).strip()[:80]
        if not name:
            return "", 204
        reason = str(data.get("reason", "")).strip()[:240]
        payload: dict = {"inspiration_award": name, "text": name}
        log_entry: dict = {"inspiration_award": name, "text": name}
        if reason:
            payload["reason"] = reason
            log_entry["reason"] = reason
        with _text_log_lock:
            _text_log.append(log_entry)
        with _tail_lock:
            _tail_buffer.append(log_entry)
        _persist_log()
        _persist_tail()
        _broadcast(payload)
        # Also update player inspiration state in stats.
        # NOTE: _persist_stats() acquires _stats_lock internally — capture the snapshot
        # inside the lock, then call persist/broadcast OUTSIDE to avoid deadlock.
        stats_snapshot = None
        with _stats_lock:
            players = _current_stats.setdefault("players", [])
            match = next((p for p in players if p.get("name", "").lower() == name.lower()), None)
            if match:
                match["inspiration"] = True
                stats_snapshot = dict(_current_stats)
        if stats_snapshot is not None:
            _persist_stats()
            _broadcast({"stats": stats_snapshot})
        return "", 204

    if is_xp_award:
        xp_data = data.get("xp_award", {})
        if not isinstance(xp_data, dict):
            return "", 204
        payload = {"xp_award": xp_data, "text": xp_data.get("summary", "")}
        log_entry = {"xp_award": xp_data, "text": xp_data.get("summary", "")}
        with _text_log_lock:
            _text_log.append(log_entry)
        with _tail_lock:
            _tail_buffer.append(log_entry)
        _persist_log()
        _persist_tail()
        _broadcast(payload)
        return "", 204

    # Player/npc/dice/tutor/action text comes from send.py (no ANSI/chrome) — light clean only.
    # DM narration may come from wrapper.py — full clean.
    cleaned = raw.strip() if (is_action or is_player or is_npc or is_dice or is_tutor) else _clean(raw)
    if not cleaned.strip():
        return "", 204

    payload: dict = {"text": cleaned}

    if is_action:
        payload["action"] = data["action"]
    elif is_player:
        payload["player"] = data["player"]
    elif is_npc:
        payload["npc"] = data["npc"]
    elif is_dice:
        payload["dice"] = True
    elif is_tutor:
        payload["tutor"] = True
    else:
        # Scene detection only on DM narration
        scene = _detect_scene(cleaned)
        if scene:
            payload["scene"] = scene
            if _audio:
                _audio.on_scene_change(scene["name"])
        # SFX scan on all non-player text
        if _audio:
            _audio.on_text(cleaned)

    # Store full typed payload so replay preserves action/player/npc/dice/tutor context
    log_entry: dict = {"text": cleaned}
    if is_action:
        log_entry["action"] = data["action"]
    elif is_player:
        log_entry["player"] = data["player"]
    elif is_npc:
        log_entry["npc"] = data["npc"]
    elif is_dice:
        log_entry["dice"] = True
    elif is_tutor:
        log_entry["tutor"] = True

    # Stamp campaign on tail entries to prevent bleed when switching campaigns
    try:
        _camp_stamp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if _camp_stamp:
            log_entry["_camp"] = _camp_stamp
    except Exception:
        pass

    with _text_log_lock:
        _text_log.append(log_entry)
    with _tail_lock:
        _tail_buffer.append(log_entry)

    _persist_log()
    _persist_tail()
    _broadcast(payload)
    return "", 204


@app.route("/stats", methods=["POST"])
def stats():
    """Receive character/combat stat updates. Merges players by name, replaces turn_order.

    Pass replace_players=true to replace the entire player list (use on /dnd load to
    prevent stale characters from a previous campaign persisting in the sidebar).
    """
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    if not data:
        return "", 204

    _effect_expire_events: list[dict] = []
    with _stats_lock:
        if "players" in data:
            # replace_players=true wipes the list first — used on campaign load
            if data.get("replace_players"):
                _current_stats["players"] = []
            existing_players: list = _current_stats.setdefault("players", [])
            for incoming in data["players"]:
                name = incoming.get("name")
                if not name:
                    continue
                match = next((p for p in existing_players if p.get("name") == name), None)
                # Keys prefixed with _ are mutation ops, not stored fields
                _MUTATION_KEYS = {
                    "_inventory_add", "_inventory_remove",
                    "_conditions_add", "_conditions_remove",
                    "_slot_use", "_slot_restore",
                    "_hd_use", "_hd_restore",
                    "_effect_start", "_effect_end",
                    "_sheet_spells",
                    "_milestone_inc", "_milestone_dec",
                }
                if match:
                    for key, val in incoming.items():
                        if key == "_inventory_add":
                            inv = match.setdefault("sheet", {}).setdefault("inventory", [])
                            if val not in inv:
                                inv.append(val)
                        elif key == "_inventory_remove":
                            sheet = match.get("sheet", {})
                            sheet["inventory"] = [
                                i for i in sheet.get("inventory", [])
                                if i.lower() != str(val).lower()
                            ]
                        elif key == "_conditions_add":
                            conds = match.setdefault("conditions", [])
                            if val not in conds:
                                conds.append(val)
                        elif key == "_conditions_remove":
                            match["conditions"] = [
                                c for c in match.get("conditions", [])
                                if c.lower() != str(val).lower()
                            ]
                        elif key == "_slot_use":
                            slots = match.setdefault("spell_slots", {})
                            lvl = str(val)
                            slot = slots.setdefault(lvl, {"used": 0, "max": 0})
                            _normalize_slot(slot)
                            slot["used"] = min(slot["used"] + 1, slot.get("max", 99))
                        elif key == "_slot_restore":
                            slots = match.setdefault("spell_slots", {})
                            lvl = str(val)
                            slot = slots.setdefault(lvl, {"used": 0, "max": 0})
                            _normalize_slot(slot)
                            slot["used"] = max(slot["used"] - 1, 0)
                        elif key == "_hd_use":
                            hd = match.setdefault("hit_dice", {"remaining": 0, "max": 0})
                            hd["remaining"] = max(hd.get("remaining", 0) - 1, 0)
                        elif key == "_hd_restore":
                            hd = match.setdefault("hit_dice", {"remaining": 0, "max": 0})
                            hd["remaining"] = min(
                                hd.get("remaining", 0) + int(val),
                                hd.get("max", 99)
                            )
                        elif key == "_effect_start":
                            # val is an effect dict: {name, duration_type, ...}
                            spell_name = val.get("name", "")
                            effects = match.setdefault("effects", [])
                            # Replace any existing effect with the same name
                            match["effects"] = [
                                e for e in effects
                                if e.get("name", "").lower() != spell_name.lower()
                            ]
                            match["effects"].append(val)
                            # Sync concentration field if this is a conc effect
                            if val.get("concentration") and spell_name:
                                match["concentration"] = spell_name
                        elif key == "_effect_end":
                            # val is the spell name string
                            spell_lower = str(val).lower()
                            removed = [
                                e for e in match.get("effects", [])
                                if e.get("name", "").lower() == spell_lower
                            ]
                            match["effects"] = [
                                e for e in match.get("effects", [])
                                if e.get("name", "").lower() != spell_lower
                            ]
                            # If the ended effect was concentration, also clear it
                            if removed and any(e.get("concentration") for e in removed):
                                if match.get("concentration", "").lower() == spell_lower:
                                    match["concentration"] = None
                        elif key == "_sheet_spells":
                            # Patch only the spells sub-key inside sheet
                            sheet = match.setdefault("sheet", {})
                            sheet["spells"] = val
                        elif key == "inspiration" and val is False:
                            match["inspiration"] = False
                        elif key == "_milestone_inc":
                            # Stack-based reward counter — Inspiration variants,
                            # homebrew Hero Coins, Bardic Inspiration tokens, etc.
                            # The label string is the value; per-label cap optional.
                            label = str(val) or "Milestone"
                            ms = match.setdefault("milestones", {})
                            cap = match.get("milestone_caps", {}).get(label, 99)
                            ms[label] = min(ms.get(label, 0) + 1, cap)
                        elif key == "_milestone_dec":
                            label = str(val) or "Milestone"
                            ms = match.setdefault("milestones", {})
                            ms[label] = max(ms.get(label, 0) - 1, 0)
                            if ms.get(label, 0) == 0:
                                ms.pop(label, None)
                        elif isinstance(val, dict) and isinstance(match.get(key), dict):
                            match[key].update(val)
                        else:
                            match[key] = val
                else:
                    # Strip mutation ops — they're meaningless for new players
                    existing_players.append(
                        {k: v for k, v in incoming.items() if k not in _MUTATION_KEYS}
                    )

        # turn_order replaces entirely (None = clear); also ticks round-based effects
        _effect_expire_events: list[dict] = []
        if "turn_order" in data:
            new_to = data["turn_order"]
            _current_stats["turn_order"] = new_to
            # Decrement round-based effects for the actor whose turn just started
            if new_to and isinstance(new_to, dict) and new_to.get("current"):
                actor = new_to["current"].lower()
                for p in _current_stats.get("players", []):
                    if p.get("name", "").lower() != actor:
                        continue
                    kept, expired = [], []
                    for eff in p.get("effects", []):
                        if eff.get("duration_type") == "rounds":
                            eff = dict(eff)  # don't mutate in-place
                            eff["duration_remaining"] = max(0, eff.get("duration_remaining", 1) - 1)
                            if eff["duration_remaining"] <= 0:
                                expired.append(eff)
                            else:
                                kept.append(eff)
                        else:
                            kept.append(eff)
                    p["effects"] = kept
                    for eff in expired:
                        was_conc = eff.get("concentration", False)
                        if was_conc and p.get("concentration", "").lower() == eff["name"].lower():
                            p["concentration"] = None
                        _effect_expire_events.append({
                            "owner": p["name"],
                            "name": eff["name"],
                            "was_concentration": was_conc,
                        })

        # world_time replaces entirely
        if "world_time" in data:
            _current_stats["world_time"] = data["world_time"]

        # factions replaces entirely ([] clears)
        # Validate: default missing standing to "Neutral" and warn so the root
        # cause (DM omitting the field when building JSON from state.md prose)
        # is surfaced in logs without silently showing "—" in the sidebar.
        if "factions" in data:
            validated_factions = []
            for f in (data["factions"] or []):
                if not isinstance(f, dict):
                    continue
                if f.get("name") and not f.get("standing"):
                    print(
                        f"[WARN] faction '{f['name']}' missing standing field — "
                        "defaulting to Neutral. Push with standing: Allied/Friendly/"
                        "Neutral/Suspicious/Hostile to show correct colour.",
                        file=sys.stderr,
                    )
                    f = dict(f)
                    f["standing"] = "Neutral"
                validated_factions.append(f)
            _current_stats["factions"] = validated_factions

        # quests replaces entirely ([] clears)
        if "quests" in data:
            _current_stats["quests"] = data["quests"]

        current = dict(_current_stats)

    # autorun_waiting / autorun_cycle — display-only signals, not stored in stats
    if "autorun_waiting" in data:
        if not data["autorun_waiting"]:
            # Turn resolved — clear stored cycle so reconnecting clients don't see stale pie
            global _autorun_cycle
            with _autorun_cycle_lock:
                _autorun_cycle = None
        _broadcast({"autorun_waiting": bool(data["autorun_waiting"])})
        if not any(k in data for k in ("players", "turn_order", "world_time", "factions",
                                        "quests", "replace_players", "sheet", "autorun_cycle")):
            return "", 204

    if "autorun_cycle" in data:
        with _autorun_cycle_lock:
            _autorun_cycle = data["autorun_cycle"]
        _broadcast({"autorun_cycle": data["autorun_cycle"]})
        if not any(k in data for k in ("players", "turn_order", "world_time", "factions",
                                        "replace_players", "sheet", "autorun_threshold")):
            return "", 204

    if "autorun_threshold" in data:
        global _autorun_threshold
        val = data["autorun_threshold"]
        _autorun_threshold = int(val) if val is not None else None
        _broadcast({"autorun_threshold": _autorun_threshold})
        if not any(k in data for k in ("players", "turn_order", "world_time", "factions",
                                        "replace_players", "sheet")):
            return "", 204

    # Write active campaign name so dm_help.py always reads the current campaign.
    # Also reload the tail buffer from the new campaign's session_tail.json so
    # display replay at /dnd load shows the correct campaign's last session.
    if "campaign" in data:
        try:
            with open(CAMP_FILE, "w", encoding="utf-8") as f:
                f.write(str(data["campaign"]).strip())
            _load_tail()
        except Exception:
            pass
        # Resolve and stash the ruleset for this campaign so the sidebar badge
        # can render. Defaults to '2014' for legacy campaigns predating the
        # ruleset field. Wrapped in try/except so a missing paths import or
        # malformed state.md never breaks the stats endpoint.
        try:
            from paths import campaign_ruleset as _campaign_ruleset
            _rs = _campaign_ruleset(str(data["campaign"]).strip())
            with _stats_lock:
                _current_stats["ruleset"] = _rs
            current = dict(_current_stats)
        except Exception:
            pass

    # Explicit ruleset override (e.g. push_stats.py --ruleset 2024)
    if "ruleset" in data:
        rs_in = str(data.get("ruleset") or "").strip()
        if rs_in in ("2014", "2024"):
            with _stats_lock:
                _current_stats["ruleset"] = rs_in
            current = dict(_current_stats)

    _persist_stats()
    _broadcast({"stats": current})
    # Broadcast any round-based effect expiries after the stats update
    for evt in _effect_expire_events:
        _broadcast({"effect_expired": evt})

    # Update expected player count for staged-input auto-trigger
    global _expected_count
    with _stats_lock:
        players = _current_stats.get("players", [])
    _expected_count = max(1, len(players))

    return "", 204


@app.route("/effects/expire", methods=["POST"])
def effects_expire():
    """Called by browser when a time-based effect countdown reaches zero.
    Removes the effect from stats, clears concentration if applicable,
    and broadcasts effect_expired to all connected clients.
    """
    if not _token_ok():
        return "Forbidden", 403
    data  = request.get_json(silent=True) or {}
    owner = data.get("owner", "").strip()
    name  = data.get("name", "").strip()
    if not owner or not name:
        return "", 400

    expire_evt = None
    with _stats_lock:
        for p in _current_stats.get("players", []):
            if p.get("name", "").lower() != owner.lower():
                continue
            was_conc   = False
            new_effects = []
            for e in p.get("effects", []):
                if e.get("name", "").lower() == name.lower():
                    was_conc = e.get("concentration", False)
                    if was_conc and p.get("concentration", "").lower() == name.lower():
                        p["concentration"] = None
                else:
                    new_effects.append(e)
            p["effects"] = new_effects
            expire_evt = {"owner": p["name"], "name": name, "was_concentration": was_conc}
            break
        current = dict(_current_stats)

    if expire_evt:
        _broadcast({"effect_expired": expire_evt})
    _broadcast({"stats": current})
    _persist_stats()
    return "", 204


@app.route("/audio-toggle", methods=["POST"])
def audio_toggle():
    """Enable/disable ambient or SFX from the browser toggle switches.

    Body: {"ambient": true|false, "sfx": true|false}  (either or both keys)
    Response: {"ambient": bool, "sfx": bool, "available": bool}
    Broadcasts audio_state to all connected browsers so every device syncs.
    """
    data = request.get_json(silent=True) or {}
    if _audio:
        if "sfx" in data:
            _audio.set_sfx(bool(data["sfx"]))
        state = _audio.get_state()
    else:
        state = {"sfx": False, "available": False}
    return state, 200


@app.route("/narration-pref", methods=["POST"])
def narration_pref():
    """Set the narration-length target the DM aims for each turn.

    Body: {"target_words": int}.  0 clears the preference. Persisted to the
    runtime dir as a plain integer; check_input.py reads it and prepends a
    directive to queued player input so the DM honors it that turn — no
    separate file read required on the DM side.
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr or "?"):
        return "Rate limited", 429
    data = request.get_json(silent=True) or {}
    try:
        n = int(data.get("target_words", 0))
    except (TypeError, ValueError):
        n = 0
    n = max(0, min(5000, n))
    pref = rt("narration_target")
    try:
        if n:
            with open(pref, "w", encoding="utf-8") as f:
                f.write(str(n))
        elif os.path.exists(pref):
            os.remove(pref)
    except OSError:
        pass
    return {"target_words": n}, 200


@app.route("/roll-pref", methods=["POST"])
def roll_pref():
    """Per-character roll preference. Body: {"character": str, "mode": "auto"|"players"}.

    Persisted to runtime roll_prefs.json; check_input.py surfaces each override as a
    [[<Char> roll mode: …]] directive so the DM honors it for that character,
    overriding the campaign-wide roll_mode in state.md.

    The character name is validated against the active party via _char_ok before
    persistence — otherwise a crafted value could smuggle prompt text into the DM
    through the [[<Char> roll mode: …]] template that check_input.py emits.
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr or "?"):
        return "Rate limited", 429
    data = request.get_json(silent=True) or {}
    char = (data.get("character") or "").strip()
    mode = (data.get("mode") or "").strip().lower()
    if not char or mode not in ("auto", "players"):
        return {"ok": False}, 400
    with _stats_lock:
        known = {p["name"] for p in _current_stats.get("players", [])}
    if not _char_ok(char, known):
        return "Forbidden", 403
    pref = rt("roll_prefs.json")
    try:
        prefs = {}
        if os.path.exists(pref):
            with open(pref, encoding="utf-8") as f:
                prefs = json.load(f)
        prefs[char] = mode
        with open(pref, "w", encoding="utf-8") as f:
            json.dump(prefs, f)
    except (OSError, ValueError):
        pass
    return {"ok": True, "character": char, "mode": mode}, 200


# ─── Narrator voice (Gemini Flash TTS) ────────────────────────────────────────
# Voice selection persists per-campaign in state.md → ## Session Flags →
# `tts_voice: <name>`. Read at /index render, written by POST /voice.

_VOICE_PAT = re.compile(r"^\s*tts_voice:\s*([A-Za-z]+)\s*$", re.MULTILINE)


def _active_campaign_name() -> Optional[str]:
    try:
        return open(CAMP_FILE, encoding="utf-8").read().strip() or None
    except OSError:
        return None


def _read_narrator_voice() -> str:
    """Return the active campaign's tts_voice, or the module default."""
    if _tts is None:
        return ""
    name = _active_campaign_name()
    if not name:
        return _tts.DEFAULT_VOICE
    try:
        state = _find_campaign(name) / "state.md"
        if not state.exists():
            return _tts.DEFAULT_VOICE
        text = state.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return _tts.DEFAULT_VOICE
    m = _VOICE_PAT.search(text)
    if not m:
        return _tts.DEFAULT_VOICE
    v = m.group(1).strip()
    return v if v in _tts.VALID_VOICES else _tts.DEFAULT_VOICE


def _write_narrator_voice(voice: str) -> bool:
    """Persist tts_voice to the active campaign's state.md → ## Session Flags."""
    if _tts is None or voice not in _tts.VALID_VOICES:
        return False
    name = _active_campaign_name()
    if not name:
        return False
    try:
        state = _find_campaign(name) / "state.md"
        text = state.read_text(encoding="utf-8", errors="replace") if state.exists() else ""
    except (OSError, ValueError):
        return False

    new_line = f"tts_voice: {voice}"
    if _VOICE_PAT.search(text):
        text = _VOICE_PAT.sub(new_line, text, count=1)
    else:
        # Append under ## Session Flags. If the section is missing, append at EOF.
        if "## Session Flags" in text:
            # Insert right after the header line. Keep the existing template
            # comment if present, but place the flag immediately under it.
            text = re.sub(
                r"(## Session Flags\n(?:\*\(.*?\)\*\n)?)",
                r"\1" + new_line + "\n",
                text,
                count=1,
            )
        else:
            sep = "" if text.endswith("\n") else "\n"
            text = f"{text}{sep}\n## Session Flags\n{new_line}\n"

    try:
        state.write_text(text, encoding="utf-8")
        return True
    except OSError:
        return False


@app.route("/tts", methods=["POST"])
def tts_synthesize():
    """Synthesize a narrator/NPC block to L16 PCM.

    Body: {"text": str, "voice": str (optional)}
    Response: raw L16 PCM bytes, Content-Type: audio/L16;codec=pcm;rate=24000
    Failures: 503 (no key / module unavailable), 400 (bad input), 502 (upstream)
    """
    if _tts is None:
        return "TTS module unavailable", 503
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr or "?"):
        return "Rate limited", 429
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    voice = (data.get("voice") or _tts.DEFAULT_VOICE).strip()
    if not text:
        return "empty text", 400
    if len(text) > _tts.MAX_TEXT_CHARS:
        text = text[: _tts.MAX_TEXT_CHARS]
    if voice not in _tts.VALID_VOICES:
        voice = _tts.DEFAULT_VOICE
    if _tts.key_source() == "unset":
        return "TTS not configured (see docs/SKILL-tts.md)", 503
    try:
        pcm = _tts.synthesize_strict(text, voice)
    except _tts.TtsError as e:
        return f"TTS upstream: {e}", 502
    return Response(
        pcm,
        mimetype="audio/L16;codec=pcm;rate=24000",
        headers={
            "X-Audio-Chars": str(len(text)),
            "X-Audio-Voice": voice,
            "Cache-Control": "no-store",
        },
    )


@app.route("/voice", methods=["POST"])
def tts_voice():
    """Persist narrator voice selection for the active campaign.

    Body: {"voice": str}
    Response: {"voice": str, "persisted": bool}
    """
    if _tts is None:
        return jsonify({"voice": "", "persisted": False}), 503
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    voice = (data.get("voice") or "").strip()
    if voice not in _tts.VALID_VOICES:
        return jsonify({"error": "invalid voice"}), 400
    ok = _write_narrator_voice(voice)
    return jsonify({"voice": voice, "persisted": ok}), 200


@app.route("/audio/sfx/<name>")
def audio_sfx(name):
    """Serve a synthesized SFX WAV for the given effect name."""
    if not _audio:
        return "Audio not available", 503
    wav = _audio.get_sfx_wav(name)
    if wav is None:
        return "Not found", 404
    return Response(wav, mimetype="audio/wav",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.route("/clear", methods=["POST"])
def clear():
    """Wipe text log AND stats, broadcast clear to all connected browsers.

    Called on /dnd new (fresh campaign). Ensures sidebar shows no stale characters.
    """
    if not _token_ok():
        return "Forbidden", 403
    global _scene_buffer, _current_stats
    with _text_log_lock:
        _text_log.clear()
    with _stats_lock:
        _current_stats = {}
    _scene_buffer = []
    for path in (LOG_FILE, STATS_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    _broadcast({"clear": True})
    return "", 204


@app.route("/help-request", methods=["POST"])
def help_request():
    """Spawn dm_help.py to generate and send an on-demand DM hint.

    Protected by an O_EXCL lock file — concurrent requests return 409
    so multiple players clicking the button never duplicates execution.
    Lock is released by dm_help.py in its finally block.
    """
    if not _token_ok():
        return "Forbidden", 403

    # Atomic lock: O_EXCL fails if file already exists — no race condition
    try:
        fd = os.open(HELP_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return "Already running", 409

    # Read active campaign name
    try:
        campaign = open(CAMP_FILE, encoding="utf-8").read().strip()
    except FileNotFoundError:
        os.unlink(HELP_LOCK)
        return "No active campaign", 400

    if not campaign:
        os.unlink(HELP_LOCK)
        return "No active campaign", 400

    dm_help_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dm_help.py")
    subprocess.Popen(
        [sys.executable, dm_help_py, "--campaign", campaign],
        close_fds=True,
        start_new_session=True,
    )
    return "", 202


@app.route("/player-input", methods=["POST"])
def player_input():
    """Queue a player action submitted from the display companion.

    Body: {"character": "Mira", "text": "I draw my rapier", "hold": false}
    Broadcasts pending_input event to all connected browsers.
    """
    if not _token_ok():
        return "Forbidden", 403

    import time
    data = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", "Party"))[:50]
    text = str(data.get("text", ""))[:500]
    hold = bool(data.get("hold", False))

    # Strip shell metacharacters — input is player dialogue/action, not commands
    text = re.sub(r"[`\\$]", "", text).strip()
    if not text:
        return "empty", 400

    entry = {
        "character": character,
        "text": text,
        "hold": hold,
        "timestamp": time.time(),
    }

    with _input_lock:
        _input_queue.append(entry)
        current = list(_input_queue)

    _persist_input_queue()
    _broadcast({"pending_input": current})
    return "", 204


@app.route("/player-input/dice", methods=["POST"])
def player_dice():
    """Server-side dice roll submitted from a player's phone.

    Body: {"character": "Piper", "spec": "1d20", "modifier": 5,
           "advantage": "normal" | "advantage" | "disadvantage",
           "label": "Stealth check"  (optional)}

    Rolls server-side (secrets.randbelow → uniform, non-spoofable), broadcasts
    a dice-typed entry on the feed, and returns the result so the phone can
    finish its slot-machine animation on the real value.
    """
    if not _token_ok():
        return "Forbidden", 403

    data = request.get_json(force=True, silent=True) or {}
    character = re.sub(r"[`\\$]", "", str(data.get("character", "Player"))[:50]).strip() or "Player"
    spec      = str(data.get("spec", "1d20")).strip().lower()
    modifier  = int(data.get("modifier", 0) or 0)
    adv       = str(data.get("advantage", "normal")).strip().lower()
    label     = re.sub(r"[`\\$]", "", str(data.get("label", ""))[:60]).strip()
    req_id    = str(data.get("request_id", "")).strip()[:24]

    m = re.fullmatch(r"(\d{1,2})d(\d{1,3})", spec)
    if not m:
        return jsonify({"error": "bad spec"}), 400
    n_dice, n_sides = int(m.group(1)), int(m.group(2))
    if not (1 <= n_dice <= 20 and 2 <= n_sides <= 100):
        return jsonify({"error": "out of range"}), 400
    modifier = max(-100, min(100, modifier))

    def _roll_once() -> list[int]:
        return [secrets.randbelow(n_sides) + 1 for _ in range(n_dice)]

    if adv in ("advantage", "disadvantage") and spec == "1d20":
        r1, r2 = _roll_once(), _roll_once()
        chosen = max(r1[0], r2[0]) if adv == "advantage" else min(r1[0], r2[0])
        rolls  = [chosen]
        kept   = [chosen]
        both   = [r1[0], r2[0]]
    else:
        rolls = _roll_once()
        kept  = rolls
        both  = None

    subtotal = sum(kept)
    total    = subtotal + modifier
    mod_str  = (f"+{modifier}" if modifier > 0 else (str(modifier) if modifier < 0 else ""))
    breakdown = f"[{', '.join(str(r) for r in (both or rolls))}]"
    if both is not None:
        breakdown += f" → keep {kept[0]} ({adv})"
    if modifier:
        breakdown += f" {mod_str}"
    suffix = f" — {label}" if label else ""
    text   = f"{character} rolls {spec}{mod_str}: {breakdown} = {total}{suffix}"

    payload   = {"text": text, "dice": True}
    log_entry = {"text": text, "dice": True}
    try:
        _camp_stamp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if _camp_stamp:
            log_entry["_camp"] = _camp_stamp
    except Exception:
        pass

    with _text_log_lock:
        _text_log.append(log_entry)
    with _tail_lock:
        _tail_buffer.append(log_entry)
    _persist_log()
    _persist_tail()
    _broadcast(payload)

    # Correlate against any pending DM request. Case-insensitive match on the
    # character name — drop them from the request's expected-rollers set.
    pending_changed = False
    if req_id:
        with _dice_pending_lock:
            entry = _dice_pending.get(req_id)
            if entry is not None:
                ci = character.lower()
                matched = next((c for c in entry["chars"] if c.lower() == ci), None)
                if matched is not None:
                    entry["chars"].discard(matched)
                    pending_changed = True
                    if not entry["chars"]:
                        _dice_pending.pop(req_id, None)
    if pending_changed:
        _broadcast({"dice_pending": _dice_pending_snapshot()})

    return jsonify({
        "character": character,
        "spec": spec,
        "modifier": modifier,
        "advantage": adv,
        "rolls": rolls,
        "kept": kept,
        "both": both,
        "subtotal": subtotal,
        "total": total,
        "text": text,
        "request_id": req_id or None,
    }), 200


@app.route("/dice-request", methods=["POST"])
def dice_request():
    """DM-initiated dice request — broadcast to player phones (no persistence).

    Body: {"character": "Piper" | "any",
           "spec": "1d20", "modifier": 5,
           "advantage": "normal" | "advantage" | "disadvantage",
           "label": "Stealth check"  (optional),
           "dc": 15  (optional, informational)}

    Phones bound to ?character=<name> match case-insensitively. "any" / ""
    targets every phone. No state stored — late-joining phones will not see
    requests issued before they connected.
    """
    if not _token_ok():
        return "Forbidden", 403

    import time
    data = request.get_json(force=True, silent=True) or {}
    raw_char  = data.get("characters") if "characters" in data else data.get("character", "any")
    if isinstance(raw_char, list):
        chars = [str(c).strip() for c in raw_char if str(c).strip()]
    else:
        chars = [c.strip() for c in re.sub(r"[`\\$]", "", str(raw_char))[:200].split(",") if c.strip()]
    if not chars:
        chars = ["any"]

    spec      = str(data.get("spec", "1d20")).strip().lower()
    modifier  = int(data.get("modifier", 0) or 0)
    adv       = str(data.get("advantage", "normal")).strip().lower()
    label     = re.sub(r"[`\\$]", "", str(data.get("label", ""))[:60]).strip()
    dc        = data.get("dc")

    if not re.fullmatch(r"\d{1,2}d\d{1,3}", spec):
        return jsonify({"error": "bad spec"}), 400
    if adv not in ("normal", "advantage", "disadvantage"):
        adv = "normal"
    modifier = max(-100, min(100, modifier))
    dc_val   = int(dc) if isinstance(dc, (int, float)) else None

    request_id = secrets.token_hex(6)

    # Only register pending entries for explicit named targets. "any" is fire-and-forget.
    trackable = [c for c in chars if c.lower() != "any"]
    if trackable:
        with _dice_pending_lock:
            _dice_pending[request_id] = {
                "chars": set(trackable),
                "meta": {"spec": spec, "modifier": modifier, "advantage": adv, "label": label, "dc": dc_val},
                "started_at": time.time(),
            }
        _broadcast({"dice_pending": _dice_pending_snapshot()})

    # Targets with no live phone bound → the main display should roll on-screen.
    onscreen_targets = [c for c in chars if c.lower() != "any" and not _phone_present(c)]
    payload = {
        "dice_request": {
            "request_id": request_id,
            "characters": chars,
            "character": chars[0] if len(chars) == 1 else "any",   # legacy single-target field
            "onscreen_targets": onscreen_targets,
            "spec": spec,
            "modifier": modifier,
            "advantage": adv,
            "label": label,
            "dc": dc_val,
        }
    }
    _broadcast(payload)
    return jsonify({
        "request_id": request_id,
        "pending": sorted(trackable),
        "complete": not trackable,
    }), 200


@app.route("/dice-request/<request_id>", methods=["GET"])
def dice_request_status(request_id):
    """Poll a dice request's completion state.

    Returns 200 with {complete, pending, label, started_at}. A request that
    never existed (or has already fully drained) reports complete=True with
    an empty pending list — send.py --wait treats both identically.
    """
    if not _token_ok():
        return "Forbidden", 403
    with _dice_pending_lock:
        entry = _dice_pending.get(request_id)
        if entry is None or not entry["chars"]:
            return jsonify({"complete": True, "pending": []}), 200
        return jsonify({
            "complete": False,
            "pending": sorted(entry["chars"]),
            "label": entry["meta"].get("label", ""),
            "started_at": entry["started_at"],
        }), 200


@app.route("/dice-request/<request_id>", methods=["DELETE"])
def dice_request_cancel(request_id):
    """Cancel a pending dice request (DM gave up waiting / moved on)."""
    if not _token_ok():
        return "Forbidden", 403
    with _dice_pending_lock:
        _dice_pending.pop(request_id, None)
    _broadcast({"dice_pending": _dice_pending_snapshot(), "dice_request_cancelled": request_id})
    return "", 204


@app.route("/character/<character>", methods=["GET"])
def get_character_sheet(character):
    """Return the markdown content of a PC sheet for the active campaign.

    Used by the phone's Character tab. Resolves the active campaign from
    CAMP_FILE, then reads:
        <DND_CAMPAIGN_ROOT>/campaigns/<campaign>/characters/<character>.md

    Falls back to the global roster at ~/.claude/dnd/characters/<character>.md
    if the campaign-side file is missing — useful when the character was just
    imported but not yet replicated.

    Returns text/markdown so the phone can render in JS without server-side
    dependencies (no `markdown` lib required).
    """
    if not _token_ok():
        return "Forbidden", 403

    safe = re.sub(r"[^A-Za-z0-9 _-]", "", character).strip()[:50]
    if not safe:
        return "Bad character name", 400

    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        camp = ""
    # Sanitise the campaign name with the same allowlist + length cap as the
    # character argument. CAMP_FILE is writable by anyone inside the LAN+token
    # trust boundary (via push_stats.py --set-campaign), so a malicious value
    # here could pivot to arbitrary `<name>.md` reads via os.path.join.
    camp = re.sub(r"[^A-Za-z0-9_-]", "", camp)[:50]

    root = os.environ.get("DND_CAMPAIGN_ROOT", os.path.expanduser("~/.claude/dnd"))
    candidates = []
    if camp:
        candidates.append(os.path.join(root, "campaigns", camp, "characters", f"{safe}.md"))
    candidates.append(os.path.expanduser(f"~/.claude/dnd/characters/{safe}.md"))

    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    body = f.read()
            except Exception as e:
                return f"Read error: {e}", 500
            return Response(body, mimetype="text/markdown; charset=utf-8")

    return f"No sheet found for '{safe}' in campaign '{camp}'", 404


@app.route("/device/approve", methods=["POST"])
def device_approve():
    """DM approves a pending device. Body: {"id": "<device_id>"}"""
    if not _token_ok():
        return "Forbidden", 403
    device_id = str((request.get_json(force=True, silent=True) or {}).get("id", ""))
    with _devices_lock:
        _pending_devices.pop(device_id, None)
        _approved_devices.add(device_id)
    _persist_approved_devices()
    _persist_pending_devices()
    _broadcast({"device_approved": device_id})
    return "", 204


@app.route("/device/deny", methods=["POST"])
def device_deny():
    """DM denies a pending device. Body: {"id": "<device_id>"}"""
    if not _token_ok():
        return "Forbidden", 403
    device_id = str((request.get_json(force=True, silent=True) or {}).get("id", ""))
    with _devices_lock:
        _pending_devices.pop(device_id, None)
        _denied_devices.add(device_id)
    _persist_pending_devices()
    _broadcast({"device_denied": device_id})
    return "", 204


@app.route("/player-input/stage", methods=["POST"])
def stage_input():
    """Stage a player action for review. Broadcasts staged_inputs to all displays.

    Body: {"character": "Mira", "text": "draws her rapier"}
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429

    device_id = request.headers.get("X-DND-Device", "")
    status    = _device_ok(device_id, request.remote_addr)
    if status == "denied":
        return "Forbidden", 403
    if status == "pending":
        return jsonify({"status": "pending"}), 202

    data      = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", ""))[:50].strip()
    text      = _sanitize_input(str(data.get("text", "")))

    if not character or not text:
        return "Bad Request", 400

    with _stats_lock:
        known = {p["name"] for p in _current_stats.get("players", [])}
    if not _char_ok(character, known):
        return "Forbidden", 403

    # In solo mode (1 expected player), skip the manual Ready step and auto-trigger.
    solo = (_expected_count == 1)

    with _staged_lock:
        _staged[character] = {
            "text":      text,
            "ready":     solo,
            "timestamp": _time.time(),
        }
        snap = _staged_snapshot()

    _broadcast({"staged_inputs": snap})

    if solo:
        _check_auto_trigger()

    return "", 204


@app.route("/player-input/ready", methods=["POST"])
def ready_input():
    """Toggle the ready flag for a staged character.

    Body: {"character": "Mira", "ready": true}
    Triggers auto-fire when all expected players are ready.
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429

    device_id = request.headers.get("X-DND-Device", "")
    status    = _device_ok(device_id, request.remote_addr)
    if status == "denied":
        return "Forbidden", 403
    if status == "pending":
        return jsonify({"status": "pending"}), 202

    data      = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", ""))[:50].strip()
    ready     = bool(data.get("ready", True))

    with _staged_lock:
        if character not in _staged:
            return "Not Found", 404
        _staged[character]["ready"] = ready
        snap = _staged_snapshot()

    _broadcast({"staged_inputs": snap})

    if ready:
        _check_auto_trigger()

    return "", 204


@app.route("/player-input/unstage", methods=["POST"])
def unstage_input():
    """Remove a character's staged action (e.g. player wants to edit it).

    Body: {"character": "Mira"}
    """
    if not _token_ok():
        return "Forbidden", 403

    device_id = request.headers.get("X-DND-Device", "")
    if _device_ok(device_id, request.remote_addr) != "approved":
        return "Forbidden", 403

    data      = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", ""))[:50].strip()

    with _staged_lock:
        _staged.pop(character, None)
        snap = _staged_snapshot()

    _broadcast({"staged_inputs": snap})
    return "", 204


@app.route("/player-input/skip", methods=["POST"])
def skip_input():
    """Skip a character's turn — stages a 'skips their turn' entry marked ready.

    Counts toward the auto-trigger threshold and fires auto-trigger if threshold met.
    Body: {"character": "Mira"}
    """
    if not _token_ok():
        return "Forbidden", 403

    device_id = request.headers.get("X-DND-Device", "")
    if _device_ok(device_id, request.remote_addr) != "approved":
        return "Forbidden", 403

    data      = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", ""))[:50].strip()
    if not character:
        return "Bad Request", 400

    with _stats_lock:
        known = {p["name"] for p in _current_stats.get("players", [])}
    if not _char_ok(character, known):
        return "Forbidden", 403

    with _staged_lock:
        _staged[character] = {
            "text":      "skips their turn",
            "ready":     True,
            "timestamp": _time.time(),
        }
        snap = _staged_snapshot()

    _broadcast({"staged_inputs": snap})
    _check_auto_trigger()
    return "", 204


@app.route("/queue/consumed", methods=["POST"])
def queue_consumed():
    """Called by wrapper.py after it injects .input_queue into the PTY.

    Clears the server-side queue_status and broadcasts to all clients so
    the 'Queued — fires on DM Enter' indicator disappears on every display.
    Token required (called from localhost by the wrapper, but checked for
    consistency).
    """
    if not _token_ok():
        return "Forbidden", 403
    with _queue_status_lock:
        _queue_status.clear()
    _broadcast({"queue_status": [], "dm_processing": True})
    return "", 204


@app.route("/player-input/submit-now", methods=["POST"])
def submit_now():
    """Promote .input_queue → .input_trigger for immediate injection.

    Called by the DM or Claude when they want to process queued player actions
    right now rather than waiting for the DM's next CLI Enter press.
    Token required (DM-only action).
    """
    if not _token_ok():
        return "Forbidden", 403
    try:
        content = open(QUEUE_FILE, encoding="utf-8").read()
        os.unlink(QUEUE_FILE)
    except FileNotFoundError:
        return "No queue", 204
    except Exception:
        return "Error", 500
    try:
        with open(TRIGGER_FILE, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        return "Error", 500
    return "", 204


@app.route("/player-input/drain", methods=["POST"])
def drain_player_input():
    """Read and clear the player input queue. Called by check_input.py at turn start.

    Returns the drained entries as JSON, then broadcasts pending_input: [] to
    clear the indicator on all connected displays.
    """
    if not _token_ok():
        return "Forbidden", 403

    with _input_lock:
        drained = list(_input_queue)
        _input_queue.clear()

    _persist_input_queue()
    _broadcast({"pending_input": []})
    return jsonify(drained), 200


@app.route("/stream")
def stream():
    q: queue.Queue = queue.Queue(maxsize=256)
    with _clients_lock:
        _clients.append(q)
        # Register this client's bound character (phones pass ?character=/?char=);
        # the main display passes neither. Drives dice-request phone-vs-screen routing.
        _ch = (request.args.get("character") or request.args.get("char") or "").strip().lower()[:48]
        if _ch:
            _client_chars[q] = _ch

    # Send the current scene immediately on connect so the browser
    # starts with the right background even mid-session.
    initial_scene = SCENES[_current_scene_name] | {"name": _current_scene_name}
    q.put_nowait({"scene": initial_scene})

    # Replay recent entries so late-connecting / reconnecting browsers catch up.
    # Sent as a typed batch so the browser can render each item (dm/player/dice) correctly.
    with _text_log_lock:
        recent = list(_text_log)
    if recent:
        q.put_nowait({"replay_batch": recent})

    # Send current stats so the sidebar is populated immediately on (re)connect.
    with _stats_lock:
        if _current_stats:
            q.put_nowait({"stats": dict(_current_stats)})

    # Send current input queue so the pending indicator is accurate on reconnect.
    with _input_lock:
        if _input_queue:
            q.put_nowait({"pending_input": list(_input_queue)})

    # Send current staged inputs so the panel reflects live state on reconnect.
    with _staged_lock:
        if _staged:
            q.put_nowait({"staged_inputs": _staged_snapshot()})

    # Send current queue status so the 'Queued' indicator survives page reload.
    with _queue_status_lock:
        if _queue_status:
            q.put_nowait({"queue_status": list(_queue_status)})

    # Send current pending dice requests so the "Waiting on…" badge survives reload.
    snap = _dice_pending_snapshot()
    if snap:
        q.put_nowait({"dice_pending": snap})

    # Replay every active dice_request so phones that connected *after* a DM
    # broadcast still pre-fill their pad and store the request_id. Without this,
    # a late-joining or reloaded phone rolls without a request_id, the roll logs
    # but the pending set never drains, and the "Waiting on…" banner gets stuck.
    with _dice_pending_lock:
        active = [(rid, dict(e["meta"]), sorted(e["chars"])) for rid, e in _dice_pending.items() if e["chars"]]
    for rid, meta, chars in active:
        q.put_nowait({"dice_request": {
            "request_id": rid,
            "characters": chars,
            "character": chars[0] if len(chars) == 1 else "any",
            "onscreen_targets": [c for c in chars if c.lower() != "any" and not _phone_present(c)],
            "spec": meta.get("spec", "1d20"),
            "modifier": meta.get("modifier", 0),
            "advantage": meta.get("advantage", "normal"),
            "label": meta.get("label", ""),
            "dc": meta.get("dc"),
        }})

    # Replay autorun cycle so reconnecting clients resume the countdown from correct elapsed position.
    with _autorun_cycle_lock:
        if _autorun_cycle:
            q.put_nowait({"autorun_cycle": dict(_autorun_cycle)})

    # Replay threshold so the ready counter reflects the correct target on reconnect.
    if _autorun_threshold is not None:
        q.put_nowait({"autorun_threshold": _autorun_threshold})

    # Send any pending device approval requests so the DM sees them on reconnect.
    with _devices_lock:
        for dev in list(_pending_devices.values()):
            q.put_nowait({"device_request": {"id": dev["id"], "ip": dev["ip"]}})

    def generate():
        try:
            while True:
                try:
                    payload = q.get(timeout=5)
                    yield f"data: {json.dumps(payload)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"   # prevent proxy timeout
        except GeneratorExit:
            with _clients_lock:
                try:
                    _clients.remove(q)
                except ValueError:
                    pass
                _client_chars.pop(q, None)

    resp = Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Transfer-Encoding": "chunked",
        },
    )
    # Force a single authoritative Connection header — Werkzeug otherwise
    # emits both keep-alive (ours) and close (its default), which confuses
    # transparent proxies (e.g. eero mesh routing) into buffering the stream.
    resp.headers["Connection"] = "keep-alive"
    return resp


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Wire audio SFX broadcast now that _broadcast is defined
    if _audio:
        _audio.set_broadcast(_broadcast)

    host = "0.0.0.0" if _LAN_MODE else "localhost"
    # TLS — only enabled when --tls is explicitly passed; HTTP is the default.
    # Certs are runtime state (persist across plugin updates) → rt(); .scheme is a
    # launch-time marker read by simple shell commands → stays in the code dir.
    _display_dir = os.path.dirname(os.path.abspath(__file__))
    _cert = rt("cert.pem")
    _key  = rt("key.pem")
    ssl_ctx = (_cert, _key) if (_TLS_MODE and os.path.exists(_cert) and os.path.exists(_key)) else None
    scheme  = "https" if ssl_ctx else "http"

    # Write .scheme so push_stats.py / send.py / autorun_wait.py know which to use
    try:
        with open(os.path.join(_display_dir, ".scheme"), "w", encoding="utf-8") as _sf:
            _sf.write(scheme)
    except OSError:
        pass

    if _LAN_MODE:
        print(f"DnD DM Display — LAN mode (0.0.0.0:5001) [{scheme.upper()}]")
        print(f"  Local:  {scheme}://localhost:5001")
        print("  Token stored at:", TOKEN_FILE)
        print("  POST endpoints require X-DND-Token header (send.py/push_stats.py handle this automatically)")
        print()
    else:
        print(f"DnD DM Display — Flask server starting on {scheme}://localhost:5001")
        print(f"Open {scheme}://localhost:5001 in your browser, then Chromecast the tab.")
        print()
    app.run(host=host, port=5001, threaded=True, debug=False, ssl_context=ssl_ctx)
