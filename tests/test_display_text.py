"""Short replies survive the chrome filter, and straight quotes stay straight.

WHY
===
Two display defects in how narration text reaches the page:

  * **Short replies vanished in most scripts.** `_is_chrome` drops any line of
    three characters or fewer without `[a-zA-Z]{2}` in it, as escape-code
    residue. That test is ASCII: 「好的」, 「はい。」, 「不。」, 「Да.」 and
    「Нет」 -- an NPC's whole answer -- were filtered out, while "No." stayed.
    Letters are now counted with `str.isalpha`, and in a script without case a
    single letter is already a word (不 is "no").
  * **Straight quotes rendered as closing quotes.** IM Fell English draws
    U+0022 and U+0027 with the outline of ” and ’, so "Halt," read as ”Halt,”.
    An `@font-face` that covers only those two code points, from serif fonts
    already on the machine, sits ahead of IM Fell in every stack that names
    it. No font file is shipped.

The control for the first is the residue the filter exists for: "0m", "[0m"
and ";;" must still be dropped, or the fix has simply switched the filter off.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "skills" / "dnd" / "display"
HTML = DISPLAY / "templates" / "index.html"


class ShortReplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(DISPLAY))
        sys.path.insert(0, str(ROOT / "skills" / "dnd" / "scripts"))
        spec = importlib.util.spec_from_file_location("dndapp_text", DISPLAY / "dnd-display-app.py")
        cls.m = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(cls.m)
        except Exception as exc:                      # flask/deps absent
            raise unittest.SkipTest(f"display app not importable: {exc}")

    def test_short_replies_in_every_script_are_kept(self):
        for line in ("好的", "はい。", "不。", "Да.", "Нет", "Ναι", "네.", "No.", "Hi", "ok"):
            self.assertFalse(self.m._is_chrome(line), line)

    def test_they_survive_the_full_clean_pass(self):
        text = "│ 「不。」 │\n│ Да. │\n│ No. │"
        cleaned = self.m._clean(text)
        for word in ("不。", "Да.", "No."):
            self.assertIn(word, cleaned)

    def test_escape_residue_is_still_dropped(self):
        for line in ("0m", "[0m", ";;", "x.", "─", "42"):
            self.assertTrue(self.m._is_chrome(line), line)


class StraightQuoteFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML.read_text(encoding="utf-8")

    def _face(self) -> str:
        m = re.search(r"@font-face\s*\{[^}]*'Fell Straight Quotes'[^}]*\}", self.html)
        self.assertIsNotNone(m, "no @font-face for the straight-quote face")
        return m.group(0)

    def test_face_covers_only_the_two_straight_quotes(self):
        rng = re.search(r"unicode-range:\s*([^;]+);", self._face()).group(1)
        self.assertEqual({r.strip().upper() for r in rng.split(",")}, {"U+0022", "U+0027"})

    def test_face_ships_no_font_file(self):
        src = re.search(r"src:\s*([^;]+);", self._face()).group(1)
        self.assertNotIn("url(", src)
        self.assertGreaterEqual(src.count("local("), 2)

    def test_every_im_fell_stack_puts_the_quote_face_first(self):
        stacks = re.findall(r"font-family:\s*([^;]*IM Fell English[^;]*);", self.html)
        self.assertGreaterEqual(len(stacks), 4)
        for s in stacks:
            self.assertTrue(s.strip().startswith("'Fell Straight Quotes'"), s)


if __name__ == "__main__":
    unittest.main()
