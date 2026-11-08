"""Narrator TTS via Gemini Flash TTS (Google AI Studio API key).

Server-side wrapper called by /tts in dnd-display-app.py. Reads the API key
from DND_TTS_KEY env var → GEMINI_API_KEY env var → ~/.config/claude-dnd/tts.key
(in that order). Returns None on any failure so the caller can silently
degrade to text-only narration.

stdlib only — no requests, no google-cloud-sdk, no dependencies.
Setup walkthrough: docs/SKILL-tts.md.

Synthesized clips are cached on disk, keyed by a hash of (voice, model,
text), so replaying a block plays the saved clip instead of spending the
user's Gemini quota on the same request again. The cache is bounded
(DND_TTS_CACHE_MB, default 64) and evicts least-recently-played clips.
DND_TTS_CACHE_DIR moves it; DND_TTS_CACHE_MB=0 turns it off.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# ── Configuration ───────────────────────────────────────────────────────────

GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
GEMINI_TTS_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_TTS_MODEL}:generateContent"
)

KEY_FILE = Path.home() / ".config" / "claude-dnd" / "tts.key"

# Curated 9-voice catalog. The full Gemini API exposes 30 voices; this
# subset keeps the per-block dropdown scannable. Extend VALID_VOICES and
# the matching arrays in display/templates/index.html if you want the
# full catalog.
VALID_VOICES = frozenset({
    # Male
    "Charon", "Enceladus", "Fenrir", "Umbriel",
    # Female
    "Aoede", "Gacrux", "Kore", "Vindemiatrix", "Zephyr",
})
DEFAULT_VOICE = "Enceladus"

# Gemini Flash TTS responses degrade in latency past this point and may
# truncate. Bigger blocks should be chunked by the caller; the narration
# path naturally chunks at block flush.
MAX_TEXT_CHARS = 2000

# Request timeout — Gemini Flash TTS typical latency is 1-3s; we give it
# substantial headroom for cold-start or transient slowness.
DEFAULT_TIMEOUT = 30.0


# ── Clip cache ──────────────────────────────────────────────────────────────

DEFAULT_CACHE_MB = 64


def cache_dir() -> Path:
    raw = os.environ.get("DND_TTS_CACHE_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".cache" / "claude-dnd" / "tts"


def _cache_limit_bytes() -> int:
    try:
        mb = float(os.environ.get("DND_TTS_CACHE_MB", DEFAULT_CACHE_MB))
    except ValueError:
        mb = DEFAULT_CACHE_MB
    return max(0, int(mb * 1024 * 1024))


def cache_key(voice: str, text: str, model: str = GEMINI_TTS_MODEL) -> str:
    """Hash of everything that changes the audio. The text is never a filename."""
    h = hashlib.sha256()
    for part in (voice, model, text):
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def cache_get(key: str) -> Optional[bytes]:
    if _cache_limit_bytes() <= 0:
        return None
    p = cache_dir() / f"{key}.pcm"
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    try:
        os.utime(p, None)   # mark as recently played, for eviction order
    except OSError:
        pass
    return data


def cache_put(key: str, pcm: bytes) -> None:
    """Store a clip, then evict least-recently-played clips over the bound.

    Best effort: a cache that cannot be written costs a re-synthesis later,
    never the narration now.
    """
    limit = _cache_limit_bytes()
    if limit <= 0 or not pcm or len(pcm) > limit:
        return
    d = cache_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{key}.tmp"
        tmp.write_bytes(pcm)
        os.replace(tmp, d / f"{key}.pcm")
        clips = sorted(d.glob("*.pcm"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in clips)
        for f in clips:
            if total <= limit:
                break
            total -= f.stat().st_size
            f.unlink()
    except OSError:
        pass


# ── Key resolution ──────────────────────────────────────────────────────────

def _get_api_key() -> Optional[str]:
    """Resolve the Gemini API key. Env vars take precedence over the key file."""
    for env in ("DND_TTS_KEY", "GEMINI_API_KEY"):
        v = os.environ.get(env)
        if v and v.strip():
            return v.strip()
    if KEY_FILE.exists():
        try:
            return KEY_FILE.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    return None


def key_source() -> str:
    """Return a human description of where the key came from, for diagnostics.

    Used by the verify path and the /dnd load surface — does not return the key.
    """
    for env in ("DND_TTS_KEY", "GEMINI_API_KEY"):
        v = os.environ.get(env)
        if v and v.strip():
            return f"env:{env}"
    if KEY_FILE.exists() and KEY_FILE.read_text(encoding="utf-8").strip():
        return f"file:{KEY_FILE}"
    return "unset"


# ── Synthesis ───────────────────────────────────────────────────────────────

class TtsError(Exception):
    """Raised by synthesize_strict; caught by synthesize to fail silently."""


def synthesize_strict(
    text: str,
    voice: str = DEFAULT_VOICE,
    timeout: float = DEFAULT_TIMEOUT,
    use_cache: bool = True,
) -> bytes:
    """Call Gemini Flash TTS and return raw L16 PCM bytes (24 kHz mono).

    Raises TtsError for any failure. Use synthesize() for the silent-fail path.
    """
    key = _get_api_key()
    if not key:
        raise TtsError("no api key configured")

    text = (text or "").strip()
    if not text:
        raise TtsError("empty text")
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]

    if voice not in VALID_VOICES:
        voice = DEFAULT_VOICE

    key_hash = cache_key(voice, text)
    cached = cache_get(key_hash) if use_cache else None
    if cached is not None:
        return cached

    body = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice}
                }
            },
        },
    }
    body_bytes = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        f"{GEMINI_TTS_URL}?key={key}",
        data=body_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise TtsError(f"http {resp.status}")
            raw = resp.read()
    except urllib.error.HTTPError as e:
        snippet = ""
        try:
            snippet = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        raise TtsError(f"http {e.code}: {snippet}") from e
    except urllib.error.URLError as e:
        raise TtsError(f"network: {e.reason}") from e
    except Exception as e:
        raise TtsError(f"unexpected: {e}") from e

    try:
        data = json.loads(raw)
        b64 = data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        raise TtsError(f"bad response shape: {e}") from e

    try:
        pcm = base64.b64decode(b64)
    except Exception as e:
        raise TtsError(f"bad base64: {e}") from e

    if not pcm:
        raise TtsError("empty pcm payload")
    cache_put(key_hash, pcm)
    return pcm


def synthesize(text: str, voice: str = DEFAULT_VOICE) -> Optional[bytes]:
    """Silent-fail wrapper. Returns L16 PCM bytes or None on any failure."""
    try:
        return synthesize_strict(text, voice)
    except TtsError:
        return None


# ── CLI for verification ────────────────────────────────────────────────────

def _cli() -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(
        description="Verify Gemini Flash TTS setup for the DnD display companion.",
    )
    p.add_argument(
        "--test", action="store_true",
        help="Check key source + run a synthesis call. Writes nothing.",
    )
    p.add_argument(
        "--speak", action="store_true",
        help="Also play the synthesized audio via afplay (macOS).",
    )
    p.add_argument(
        "--text", default="Hello, narrator voice test. The torchlit hall awaits.",
        help="Override the default test phrase.",
    )
    p.add_argument(
        "--voice", default=DEFAULT_VOICE,
        help=f"Voice name (default: {DEFAULT_VOICE}). Valid: {sorted(VALID_VOICES)}",
    )
    args = p.parse_args()

    if not args.test:
        p.print_help()
        return 0

    src = key_source()
    print(f"API key source: {src}")
    if src == "unset":
        print("  → no key found. See docs/SKILL-tts.md to configure one.")
        return 2

    print(f"Model: {GEMINI_TTS_MODEL}")
    print(f"Voice: {args.voice}")
    print(f"Text:  {args.text!r}")
    print("Calling Gemini Flash TTS…")
    try:
        # --test checks the key and the API, so it never answers from cache.
        pcm = synthesize_strict(args.text, args.voice, use_cache=False)
    except TtsError as e:
        print(f"  FAIL: {e}")
        return 1

    print(f"  OK — received {len(pcm)} bytes of L16 PCM (24 kHz mono).")

    if args.speak:
        import struct
        import subprocess
        import tempfile

        sample_rate = 24000
        n_samples = len(pcm) // 2
        byte_rate = sample_rate * 2
        wav_header = (
            b"RIFF"
            + struct.pack("<I", 36 + len(pcm))
            + b"WAVE"
            + b"fmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, byte_rate, 2, 16)
            + b"data"
            + struct.pack("<I", len(pcm))
        )
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_header + pcm)
            wav_path = f.name
        print(f"  Playing via afplay: {wav_path}")
        try:
            subprocess.run(["afplay", wav_path], check=False)
        except FileNotFoundError:
            print("  (afplay not found — non-macOS? PCM file kept at the path above.)")
            return 0
        os.unlink(wav_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
