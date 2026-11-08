"""Replaying a narration block does not spend the Gemini quota twice.

WHY
===
Every press of a block's play button called Gemini, so replaying a line, or a
second player pressing play on the same block, paid for the same audio again
-- and on the free tier, spent the same rate limit. Clips are now cached on
disk, keyed by a hash of (voice, model, text), bounded in size, with
least-recently-played clips evicted first.

The network is replaced with a counting fake, so each test asserts how many
requests actually left the machine rather than what the cache claims.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"

_spec = importlib.util.spec_from_file_location("tts_cache_mod", DISPLAY / "tts.py")
tts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tts)


class _FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "DND_TTS_KEY": "test-key", "DND_TTS_CACHE_DIR": self.td.name,
            "DND_TTS_CACHE_MB": "1"})
        self.env.start()
        self.calls = []

        def fake_urlopen(req, timeout=None):
            body = json.loads(req.data)
            text = body["contents"][0]["parts"][0]["text"]
            self.calls.append(text)
            pcm = (text * 50).encode("utf-8")
            payload = {"candidates": [{"content": {"parts": [
                {"inlineData": {"data": base64.b64encode(pcm).decode()}}]}}]}
            return _FakeResponse(json.dumps(payload).encode())

        self.net = mock.patch.object(tts.urllib.request, "urlopen", fake_urlopen)
        self.net.start()

    def tearDown(self):
        self.net.stop()
        self.env.stop()
        self.td.cleanup()

    def test_a_replay_makes_no_second_request(self):
        a = tts.synthesize_strict("The door creaks open.", "Kore")
        b = tts.synthesize_strict("The door creaks open.", "Kore")
        self.assertEqual(a, b)
        self.assertEqual(len(self.calls), 1)

    def test_a_different_voice_is_a_different_clip(self):
        tts.synthesize_strict("The door creaks open.", "Kore")
        tts.synthesize_strict("The door creaks open.", "Charon")
        self.assertEqual(len(self.calls), 2)

    def test_the_key_hashes_voice_model_and_text(self):
        k = tts.cache_key("Kore", "hello")
        self.assertNotEqual(k, tts.cache_key("Kore", "hello", model="other-model"))
        self.assertNotEqual(k, tts.cache_key("Charon", "hello"))
        self.assertNotIn("hello", k)
        self.assertEqual(len(k), 64)

    def test_the_cache_is_bounded(self):
        # ~30 KB a clip, so 40 clips overrun the 1 MB bound
        for i in range(40):
            tts.synthesize_strict(f"Line {i} " + "x" * 600, "Kore")
            time.sleep(0.002)   # eviction orders by mtime; keep it increasing
        total = sum(f.stat().st_size for f in pathlib.Path(self.td.name).glob("*.pcm"))
        self.assertLessEqual(total, 1024 * 1024)
        # the most recent clip survived eviction, the oldest did not
        self.assertIsNotNone(tts.cache_get(tts.cache_key("Kore", "Line 39 " + "x" * 600)))
        self.assertIsNone(tts.cache_get(tts.cache_key("Kore", "Line 0 " + "x" * 600)))

    def test_zero_turns_the_cache_off(self):
        with mock.patch.dict(os.environ, {"DND_TTS_CACHE_MB": "0"}):
            tts.synthesize_strict("Again.", "Kore")
            tts.synthesize_strict("Again.", "Kore")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(list(pathlib.Path(self.td.name).glob("*.pcm")), [])

    def test_the_self_test_always_calls_the_api(self):
        tts.synthesize_strict("Check.", "Kore")
        tts.synthesize_strict("Check.", "Kore", use_cache=False)
        self.assertEqual(len(self.calls), 2)

    def test_a_failed_call_caches_nothing(self):
        def boom(req, timeout=None):
            raise tts.urllib.error.URLError("offline")
        with mock.patch.object(tts.urllib.request, "urlopen", boom):
            self.assertIsNone(tts.synthesize("Offline line.", "Kore"))
        self.assertEqual(list(pathlib.Path(self.td.name).glob("*.pcm")), [])


if __name__ == "__main__":
    unittest.main()
