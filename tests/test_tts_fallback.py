"""A keyless install must still be able to hear the narration.

WHY
===
`/tts` returns 503 when no Gemini key is configured, and the play button's
catch block put the string "TTS 503" on the button for three seconds and gave
up. There was no second path: `speechSynthesis` appeared nowhere in the repo,
so narration audio was a paid feature by accident. The fallback speaks through
the browser's own voice instead.

Two things in it are easy to get wrong, and are what these tests check:

  * **503 and a transient error are not the same.** 503 means no key exists, so
    the server should never be asked again this session. A 429 or a dropped
    connection means try the server again on the next block. Collapsing them
    either hammers a server that cannot answer, or permanently downgrades a
    working install because one request timed out.
  * **The chunker must split text in every script we run in.** Chrome cuts a
    single utterance at roughly fifteen seconds, which is what the 220-character
    chunking is for. A terminator set of only `.!?` finds no sentence end at all
    in Japanese or Chinese prose and returns the whole paragraph as one chunk —
    walking straight into the cut-off the function exists to avoid.

Driven through node rather than asserted against the source: "the function
exists" was exactly what was true of the badge code that never ran.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = ROOT / "skills" / "dnd" / "display" / "templates" / "index.html"
NODE = shutil.which("node")

#: Pull just the fallback machinery out of the template. Narrow, so the test
#: needs no DOM beyond the two globals the picker reads.
_SLICE = re.compile(
    r"(// Free fallback: the browser's own speech synthesis.*?)\nfunction _getTtsCtx", re.S)

_STUBS = """
globalThis.document = { documentElement: { lang: DOC_LANG } };
// node >= 21 exposes a getter-only globalThis.navigator, so plain assignment
// throws. defineProperty replaces it outright.
Object.defineProperty(globalThis, 'navigator', {
  value: { language: NAV_LANG, languages: NAV_LANGS }, configurable: true, writable: true,
});
globalThis.window = {};
const _spoken = [];
globalThis.SpeechSynthesisUtterance = function (t) { this.text = t; };
globalThis.speechSynthesis = {
  speaking: false, pending: false,
  getVoices: () => VOICES,
  speak: u => _spoken.push(u),
  cancel: () => {},
  onvoiceschanged: null,
};
globalThis.window.speechSynthesis = globalThis.speechSynthesis;
"""


def _js_source() -> str:
    m = _SLICE.search(HTML.read_text(encoding="utf-8"))
    if not m:
        raise AssertionError(
            "the TTS fallback block is no longer where this test slices it; "
            "update _SLICE rather than deleting the test")
    return m.group(1)


def _run(body: str, *, voices=None, doc_lang="", nav_lang="en-US", nav_langs=None,
         source: str | None = None) -> dict:
    """Execute `body` against the real sliced source and return its JSON result."""
    if NODE is None:
        raise unittest.SkipTest("node not available")
    voices = voices if voices is not None else [{"name": "Test Voice", "lang": "en-US"}]
    nav_langs = nav_langs if nav_langs is not None else [nav_lang]
    stubs = (_STUBS
             .replace("VOICES", json.dumps(voices))
             .replace("DOC_LANG", json.dumps(doc_lang))
             .replace("NAV_LANGS", json.dumps(nav_langs))
             .replace("NAV_LANG", json.dumps(nav_lang)))
    # 'window' must exist before the sliced block runs: it tests
    # `'speechSynthesis' in window` at load.
    script = stubs + "\n" + (source if source is not None else _js_source()) + "\n" + body
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
        f.write(script)
        path = f.name
    try:
        # PIN THE ENCODING. `text=True` alone decodes the child's output with
        # the LOCALE encoding, which is ascii under the non-UTF-8 CI job and
        # cp1252 on Windows -- and the whole point of these cases is that the
        # payload is Japanese and Chinese. Without this the harness dies on
        # the very text it exists to check, which is the failure this repo
        # keeps a non-UTF-8 locale job to catch (see scripts/utf8io.py).
        r = subprocess.run([NODE, path], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        if r.returncode != 0:
            raise AssertionError(f"node failed: {(r.stderr or '').strip()[:800]}")
        out = (r.stdout or "").strip()
        if not out:
            raise AssertionError(f"node produced no stdout; stderr: {(r.stderr or '').strip()[:400]}")
        return json.loads(out.splitlines()[-1])
    finally:
        pathlib.Path(path).unlink(missing_ok=True)


class SliceSanityTests(unittest.TestCase):
    """If the slice came back empty every behavioural test below is vacuous."""

    def test_the_slice_finds_real_code(self):
        src = _js_source()
        self.assertIn("_ttsChunkText", src)
        self.assertIn("_ttsPickBrowserVoice", src)
        self.assertIn("_ttsBrowserSpeak", src)
        self.assertGreater(len(src), 1500, "slice is suspiciously short")


class WiringTests(unittest.TestCase):
    """Defined-but-never-called is the failure mode this repo has shipped before."""

    @classmethod
    def setUpClass(cls):
        cls.full = HTML.read_text(encoding="utf-8")

    def test_the_fallback_is_called_from_more_than_one_place(self):
        """It needs the pre-check (server already known keyless) AND the catch
        (this request just failed). One without the other half-works."""
        # `(?<!function )` matters: the declaration reads
        # `function _ttsBrowserSpeak(btn, text)` and matches the bare pattern,
        # so counting every occurrence stays at 2 with BOTH call sites deleted.
        # That is the exact shape this file's docstring warns about.
        calls = len(re.findall(r"(?<!function )_ttsBrowserSpeak\(btn, text\)", self.full))
        self.assertGreaterEqual(calls, 2,
                                "the fallback should be reached from both the "
                                "pre-check and the error path")

    def test_the_stop_button_cancels_the_browser_voice(self):
        """Without this, Stop silences the server path and the browser keeps
        talking over the next block."""
        self.assertIn("speechSynthesis.cancel()", self.full)
        stop = re.search(r"function _ttsStopActive\(\) \{(.*?)\n\}", self.full, re.S)
        self.assertIsNotNone(stop)
        self.assertIn("speechSynthesis.cancel()", stop.group(1))

    def test_only_503_disables_the_server_permanently(self):
        """A transient failure must not downgrade a working install."""
        self.assertIn("if (err.message === 'TTS 503') _ttsServerOk = false;", self.full)
        bare = re.findall(r"^\s*_ttsServerOk = false;", self.full, re.M)
        self.assertEqual(bare, [],
                         "an unconditional `_ttsServerOk = false` means any single "
                         "error stops using the server for the rest of the session")


class VoiceSelectionTests(unittest.TestCase):

    def test_no_locale_is_hardcoded(self):
        """A specific locale here reads that language's accent over every
        table's narration regardless of what language they are playing in."""
        src = _js_source()
        pinned = re.findall(r"""['"]([a-z]{2}-[A-Z]{2})['"]""", src)
        self.assertEqual(pinned, [],
                         f"hardcoded locale(s) in the voice picker: {pinned}")

    def test_the_page_language_wins(self):
        got = _run("console.log(JSON.stringify(_ttsPickBrowserVoice()))",
                   doc_lang="ja", nav_lang="en-US",
                   voices=[{"name": "A", "lang": "en-US"}, {"name": "B", "lang": "ja-JP"}])
        self.assertEqual(got["lang"], "ja-JP")

    def test_the_browser_chain_is_used_in_order(self):
        got = _run("console.log(JSON.stringify(_ttsPickBrowserVoice()))",
                   doc_lang="", nav_langs=["de-DE", "en-US"],
                   voices=[{"name": "A", "lang": "en-US"}, {"name": "B", "lang": "de-DE"}])
        self.assertEqual(got["lang"], "de-DE")

    def test_a_neural_voice_outranks_a_local_one(self):
        got = _run("console.log(JSON.stringify(_ttsPickBrowserVoice()))",
                   voices=[{"name": "Microsoft David", "lang": "en-US"},
                           {"name": "Microsoft Aria Online (Natural)", "lang": "en-US"}])
        self.assertIn("Natural", got["name"])

    def test_any_voice_beats_silence(self):
        """No match in any preferred language still has to speak."""
        got = _run("console.log(JSON.stringify(_ttsPickBrowserVoice()))",
                   doc_lang="ja", nav_langs=["ja-JP"],
                   voices=[{"name": "Only Voice", "lang": "fi-FI"}])
        self.assertEqual(got["lang"], "fi-FI")

    def test_no_voices_at_all_is_null_not_a_crash(self):
        got = _run("console.log(JSON.stringify({v: _ttsPickBrowserVoice()}))", voices=[])
        self.assertIsNone(got["v"])


class ScriptLanguageTests(unittest.TestCase):
    """This page's <html lang> is a static "en", so the page language alone
    sent a Japanese block to an English voice. The block's own script is the
    first preference where it names a language."""

    VOICES = [{"name": "A", "lang": "en-US"}, {"name": "B", "lang": "ja-JP"},
              {"name": "C", "lang": "ko-KR"}, {"name": "D", "lang": "zh-CN"},
              {"name": "E", "lang": "ru-RU"}]

    def _pick(self, text, nav_langs=("en-US",)):
        return _run("console.log(JSON.stringify(_ttsPickBrowserVoice(%s)))" % json.dumps(text),
                    doc_lang="en", nav_langs=list(nav_langs), voices=self.VOICES)["lang"]

    def test_the_text_script_outranks_a_static_page_language(self):
        self.assertEqual(self._pick("ランタンが揺れた。"), "ja-JP")
        self.assertEqual(self._pick("등불이 흔들린다."), "ko-KR")
        self.assertEqual(self._pick("灯笼摇曳。"), "zh-CN")

    def test_a_shared_script_takes_the_browsers_language_that_uses_it(self):
        """Cyrillic is Russian, Ukrainian, Bulgarian...: the browser's own
        Cyrillic language wins, else the script's most common one -- never
        the static English page language."""
        voices = self.VOICES + [{"name": "F", "lang": "uk-UA"}]
        got = _run("console.log(JSON.stringify(_ttsPickBrowserVoice(%s)))"
                   % json.dumps("Ліхтар блимнув."),
                   doc_lang="en", nav_langs=["uk-UA", "en-US"], voices=voices)
        self.assertEqual(got["lang"], "uk-UA")
        self.assertEqual(self._pick("Фонарь мигнул."), "ru-RU")

    def test_latin_text_leaves_the_chain_alone(self):
        self.assertEqual(self._pick("The lantern gutters."), "en-US")

    def test_the_speak_path_passes_the_text(self):
        # The call site, not the declaration: `function _ttsPickBrowserVoice(text)`
        # contains the same words, so a bare substring check passes with the
        # text never handed over.
        src = _js_source()
        self.assertRegex(src, r"const voice = _ttsPickBrowserVoice\(text\);")


class ChunkerTests(unittest.TestCase):

    CAP = 220

    def _chunks(self, text: str) -> list[str]:
        return _run("console.log(JSON.stringify(_ttsChunkText(%s)))" % json.dumps(text))

    def test_latin_prose_splits_under_the_cap(self):
        text = ("The lantern gutters and the corridor narrows. " * 20)
        chunks = self._chunks(text)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), self.CAP, f"chunk over cap: {len(c)}")

    def test_japanese_prose_splits_too(self):
        """THE regression this fix exists for. A `.!?`-only terminator set finds
        no sentence end here, returns one chunk, and hits Chrome's cut-off."""
        text = "ランタンが揺れ、通路は狭くなる。" * 30
        chunks = self._chunks(text)
        self.assertGreater(len(chunks), 1, "Japanese prose came back as a single chunk")
        for c in chunks:
            self.assertLessEqual(len(c), self.CAP)

    def test_chinese_prose_splits_too(self):
        text = "灯笼摇曳，走廊变窄了。" * 40
        chunks = self._chunks(text)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), self.CAP)

    def test_a_single_unpunctuated_run_is_still_broken_up(self):
        """Some scripts and some narrators go a long way without a full stop."""
        text = "word " * 400
        chunks = self._chunks(text)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), self.CAP)

    def test_no_text_is_dropped(self):
        text = "First sentence here. Second one follows! A third? " * 12
        chunks = self._chunks(text)
        self.assertEqual("".join(chunks.copy()).replace(" ", ""),
                         text.replace(" ", ""),
                         "the chunker lost or duplicated text")

    def test_short_text_stays_one_chunk(self):
        self.assertEqual(self._chunks("A single short line."), ["A single short line."])

    def test_empty_text_yields_nothing_speakable(self):
        self.assertEqual([c for c in self._chunks("") if c.strip()], [])


class ChunkerBreakTests(unittest.TestCase):
    """Prove the CJK terminators are load-bearing: with them removed, the
    Japanese case must fail. A guard that passes with the fix deleted is not a
    guard."""

    def test_removing_the_cjk_terminators_breaks_japanese(self):
        src = _js_source()
        crippled = src.replace("。！？؟։", "")
        self.assertNotEqual(crippled, src, "terminator set not found; update this test")
        text = "ランタンが揺れ、通路は狭くなる。" * 30
        # The length-based split still runs, so assert on what the terminators
        # actually buy: chunks that end at sentence boundaries.
        with_cjk = _run("console.log(JSON.stringify(_ttsChunkText(%s)))" % json.dumps(text))
        without = _run("console.log(JSON.stringify(_ttsChunkText(%s)))" % json.dumps(text),
                       source=crippled)
        ends_well = sum(1 for c in with_cjk if c.endswith("。"))
        ends_badly = sum(1 for c in without if c.endswith("。"))
        self.assertGreater(ends_well, ends_badly,
                           "the CJK terminators are not changing where the text splits, "
                           "so they are not doing anything")


if __name__ == "__main__":
    unittest.main()
