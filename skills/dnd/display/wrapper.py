#!/usr/bin/env python3
from __future__ import annotations
"""
wrapper.py — Claude CLI PTY wrapper with secure player-input injection

Usage:
    python3 wrapper.py [claude args...]

Spawns the claude CLI inside a PTY so the terminal experience is identical
to running `claude` directly. The wrapper's only job beyond pass-through is:

  - Poll for `.input_trigger` every 50ms
  - Validate and sanitise the payload
  - Inject it into Claude's PTY stdin (text + Enter)

Display content (narration, stats) is pushed explicitly by the DnD skill
via send.py / push_stats.py — NOT captured from the raw PTY stream.
Removing PTY→Flask forwarding eliminates the leak of tool-call JSON,
internal data structures, and UI chrome onto the display.

Security model (defence in depth):
  - Session gate: injection rejected if no active campaign file
  - Structural validation: every line must match [KnownCharacter]: text
  - Character allowlist: name must be in loaded party stats
  - Printable ASCII only: all control chars and escape sequences stripped
  - Shell metacharacter strip: $ ` \\ ; | & > < ( ) [ ] { } !
  - Per-line cap: 500 chars of action text
  - Total payload cap: 1500 chars
  - Audit log: every injection written to input_log.json with timestamp
  - Nothing is echoed back to attacker on rejection — silent drop
"""

import fcntl
import json
import os
import re
import select
import signal
import subprocess
import sys
import termios
import time
import tty
import ssl
import urllib.request

_HERE          = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt          # writable runtime dir (update-safe)

# Windows CJK fix: piped stdout defaults to the system codepage (cp936/GBK),
# which garbles or crashes on Chinese — force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
TRIGGER_FILE   = rt(".input_trigger")
QUEUE_FILE     = rt(".input_queue")
STATS_FILE     = rt("stats.json")
CAMP_FILE      = rt(".campaign")
AUDIT_LOG      = rt("input_log.json")
TOKEN_FILE     = rt(".token")
DISPLAY_URL    = "https://127.0.0.1:5001"

# Self-signed cert — skip verification for localhost
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

POLL_INTERVAL  = 0.05   # 50ms trigger file poll


def _read_token() -> str:
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _notify_consumed() -> None:
    """Tell the display server the queue was injected — clears the Queued indicator."""
    try:
        token = _read_token()
        req = urllib.request.Request(
            f"{DISPLAY_URL}/queue/consumed",
            data=b"",
            method="POST",
            headers={"X-DND-Token": token},
        )
        urllib.request.urlopen(req, timeout=1, context=_SSL_CTX)
    except Exception:
        pass  # display may not be running — not critical


# ─── Trigger sanitisation ─────────────────────────────────────────────────────

_PRINTABLE    = re.compile(
    "[^"
    "\x20-\x7E"
    " -ɏ"             # Latin-1 + Latin Extended A/B
    "Ͱ-Ͽ"             # Greek
    "Ѐ-ӿ"             # Cyrillic
    "֐-׿"             # Hebrew
    "؀-ۿ"             # Arabic
    "ݐ-ݿ"             # Arabic Supplement
    "ऀ-ॿ"             # Devanagari
    "ঀ-৿"             # Bengali
    "஀-௿"             # Tamil
    "ఀ-౿"             # Telugu
    "฀-๿"             # Thai
    "Ḁ-ỿ"             # Latin Extended Additional (Vietnamese)
    "　-〿"             # CJK Symbols
    "぀-ゟ"             # Hiragana
    "゠-ヿ"             # Katakana
    "㐀-䶿"             # CJK Ext A
    "一-鿿"             # CJK Unified
    "가-힯"             # Hangul
    "＀-￯"             # Halfwidth / Fullwidth
    "]"
)
_SHELL_CHARS  = re.compile(r'[$`\\;|&><()\[\]{}!]')  # no shell metacharacters
_CHAR_NAME_RE = re.compile(r"^\w[\w '\-]{0,48}\w$|^\w{1,2}$", re.UNICODE)
_ACTION_LINE  = re.compile(r"^\[([^\]]{1,50})\]:\s*(.{1,500})$")

_MAX_LINES    = 8     # sanity cap — no party this large exists
_MAX_LINE_LEN = 560   # [Name]: + 500 chars + slack
_MAX_TOTAL    = 1500  # total payload cap


def _known_chars() -> set:
    """Load party member names from stats.json. Empty set = bypass name check."""
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            stats = json.load(f)
        return {p["name"] for p in stats.get("players", [])}
    except Exception:
        return set()


def _sanitize(raw: str) -> str | None:
    """
    Validate and sanitize a trigger payload.

    Returns the sanitized string ready to inject, or None (silent reject).
    A single failing line rejects the *entire* payload — no partial injection.
    """
    # Session gate — no active campaign, no injection
    if not os.path.exists(CAMP_FILE):
        return None

    lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
    if not lines or len(lines) > _MAX_LINES:
        return None

    known = _known_chars()
    out   = []

    for line in lines:
        if len(line) > _MAX_LINE_LEN:
            return None

        m = _ACTION_LINE.match(line)
        if not m:
            return None  # structural violation — reject all

        char_name = m.group(1).strip()
        action    = m.group(2).strip()

        if not _CHAR_NAME_RE.match(char_name):
            return None

        if known and char_name not in known and char_name != "Everybody":
            return None

        action = _SHELL_CHARS.sub("", action)
        action = _PRINTABLE.sub("", action)[:500].strip()

        if not action:
            return None

        out.append(f"[{char_name}]: {action}")

    if not out:
        return None

    result = "\n".join(out)
    return result if len(result) <= _MAX_TOTAL else None


def _audit(text: str) -> None:
    try:
        entry = {"ts": round(time.time(), 3), "text": text}
        log: list = []
        try:
            with open(AUDIT_LOG, encoding="utf-8") as f:
                log = json.load(f)
        except Exception:
            pass
        log.append(entry)
        with open(AUDIT_LOG, "w", encoding="utf-8") as f:
            json.dump(log[-200:], f, indent=2)
    except Exception:
        pass


def _inject_queue(master_fd: int) -> None:
    """Inject .input_queue content when the DM presses Enter.

    .input_queue is written by Flask when all expected players are staged and
    ready but DM-gating is active (i.e. not auto-fired immediately).
    This fires the queued player action just before the DM's own Enter is
    forwarded, so Claude sees the player action and DM message in the same turn.
    """
    if not os.path.exists(QUEUE_FILE):
        return
    raw = ""
    try:
        with open(QUEUE_FILE, encoding="utf-8") as f:
            raw = f.read()
        os.unlink(QUEUE_FILE)
    except Exception:
        try:
            os.unlink(QUEUE_FILE)
        except Exception:
            pass
        return

    sanitized = _sanitize(raw)
    if not sanitized:
        return

    body = f"\n[PLAYER ACTION — in-game only]:\n{sanitized}"
    _audit(sanitized)

    try:
        os.write(master_fd, body.encode("utf-8", errors="replace"))
        time.sleep(0.15)
        os.write(master_fd, b"\r")
        time.sleep(0.1)   # brief gap before DM's own Enter follows
    except OSError:
        pass
    else:
        _notify_consumed()


def _check_trigger(master_fd: int) -> None:
    """Poll for trigger file; if present, sanitise and inject into PTY stdin."""
    if not os.path.exists(TRIGGER_FILE):
        return

    raw = ""
    try:
        with open(TRIGGER_FILE, encoding="utf-8") as f:
            raw = f.read()
        os.unlink(TRIGGER_FILE)
    except Exception:
        try:
            os.unlink(TRIGGER_FILE)
        except Exception:
            pass
        return

    sanitized = _sanitize(raw)
    if not sanitized:
        return  # silent drop — no feedback to attacker

    # Write text then Enter as two separate calls with a brief pause.
    # This mirrors how a human types text then presses Enter.
    # \r (0x0D) is the Enter signal in raw PTY mode.
    body = f"\n[PLAYER ACTION — in-game only]:\n{sanitized}"
    _audit(sanitized)

    try:
        os.write(master_fd, body.encode("utf-8", errors="replace"))
        time.sleep(0.15)
        os.write(master_fd, b"\r")
    except OSError:
        pass
    else:
        _notify_consumed()


# ─── PTY helpers ─────────────────────────────────────────────────────────────

def _sync_winsize(src_fd: int, dst_fd: int) -> None:
    """Copy terminal window size from src to dst (SIGWINCH handler)."""
    try:
        if os.isatty(src_fd):
            size = fcntl.ioctl(src_fd, termios.TIOCGWINSZ, b"\x00" * 8)
            fcntl.ioctl(dst_fd, termios.TIOCSWINSZ, size)
    except Exception:
        pass


# ─── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        argv = ["claude"]
    if argv[0] != "claude":
        argv = ["claude"] + argv

    import pty as _pty
    master_fd, slave_fd = _pty.openpty()
    _sync_winsize(sys.stdin.fileno(), slave_fd)

    _slave = slave_fd

    def _child_setup() -> None:
        os.setsid()
        try:
            fcntl.ioctl(_slave, termios.TIOCSCTTY, 0)
        except OSError:
            pass

    env = os.environ.copy()
    env["DND_PTY_WRAPPED"] = "1"
    env["PYTHONUTF8"] = "1"   # spawned python reads UTF-8 regardless of locale

    proc = subprocess.Popen(
        argv,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        preexec_fn=_child_setup,
        env=env,
    )
    os.close(slave_fd)

    stdin_fd  = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    old_attrs = None

    if os.isatty(stdin_fd):
        old_attrs = termios.tcgetattr(stdin_fd)
        tty.setraw(stdin_fd)

    def _restore() -> None:
        if old_attrs is not None:
            try:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_attrs)
            except Exception:
                pass

    def _sigwinch(sig, frame) -> None:  # type: ignore[misc]
        _sync_winsize(stdin_fd, master_fd)

    signal.signal(signal.SIGWINCH, _sigwinch)

    try:
        while proc.poll() is None:
            try:
                r, _, _ = select.select([master_fd, stdin_fd], [], [], POLL_INTERVAL)
            except (ValueError, select.error):
                break

            if master_fd in r:
                try:
                    data = os.read(master_fd, 4096)
                    if not data:
                        break
                    os.write(stdout_fd, data)
                except OSError:
                    break

            if stdin_fd in r:
                try:
                    data = os.read(stdin_fd, 1024)
                    if data:
                        # If DM pressed Enter, inject any queued player action first
                        if b"\r" in data or b"\n" in data:
                            _inject_queue(master_fd)
                        os.write(master_fd, data)
                except OSError:
                    break

            _check_trigger(master_fd)

    except KeyboardInterrupt:
        pass
    finally:
        _restore()

    sys.exit(proc.wait() if proc.returncode is None else proc.returncode)


if __name__ == "__main__":
    main()
