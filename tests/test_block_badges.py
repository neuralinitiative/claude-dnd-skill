"""Block badges must actually run, and must not be English-only by construction.

WHY
===
The badge feature existed as three disconnected pieces: a keyword table, a
function that built an <img>, and a CSS class. `_addBlockBadge` was never called
from anywhere, and `.block-badge` had no style rule at all. So no player in any
language had ever seen one — the "English-only keyword list" was a symptom of
code that never ran.

Wiring it as written would have shipped a feature that works only for English
narration. Two things prevent that: block KIND (npc / dice / tutor) is badged
with no word list, and the semantic table can be replaced at runtime without
editing this file.
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = ROOT / "skills" / "dnd" / "display" / "templates" / "index.html"
ICONS = ROOT / "skills" / "dnd" / "display" / "icons"


class BadgeWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = HTML.read_text(encoding="utf-8")

    def test_the_badge_function_is_actually_called(self):
        """Defined-but-never-called is how this shipped dead."""
        mentions = len(re.findall(r"_addBlockBadge", self.src))
        self.assertGreaterEqual(
            mentions, 2,
            "_addBlockBadge appears once — defined and never invoked",
        )

    def test_the_badge_class_has_a_style_rule(self):
        self.assertRegex(
            self.src, r"\.block-badge\s*\{",
            ".block-badge has no CSS rule, so a created badge renders raw",
        )


class BadgeLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = HTML.read_text(encoding="utf-8")

    def test_kind_badges_consult_no_prose(self):
        block = re.search(r"const _KIND_BADGES = \{(.*?)\};", self.src, re.S)
        self.assertIsNotNone(block, "_KIND_BADGES missing")
        self.assertIn("npc-block", block.group(1))
        self.assertNotIn("includes(", block.group(1))

    def test_the_word_table_is_overridable_at_runtime(self):
        """A non-English campaign must not have to edit the display."""
        self.assertIn("DND_BLOCK_BADGES", self.src)
        fn = re.search(r"function _badgeTable\(\) \{(.*?)\n\}", self.src, re.S)
        self.assertIsNotNone(fn)
        self.assertIn("_BLOCK_BADGES", fn.group(1), "no fallback to the built-in table")

    def test_every_referenced_icon_exists(self):
        """A badge naming a missing icon renders as a broken image, silently."""
        table = re.search(r"const _BLOCK_BADGES = \[(.*?)\n\];", self.src, re.S)
        self.assertIsNotNone(table)
        icons = set(re.findall(r"icon:\s*'([a-z_]+)'", table.group(1)))
        icons |= set(re.findall(r"'(?:npc|dice|tutor)-block':\s*'([a-z_]+)'", self.src))
        self.assertTrue(icons, "no icons parsed — the scan is blind")
        for name in sorted(icons):
            self.assertTrue((ICONS / f"{name}.png").exists(), f"icons/{name}.png missing")

    def test_an_unmatched_block_gets_no_badge(self):
        fn = re.search(r"function _addBlockBadge\(el\) \{(.*?)\n\}", self.src, re.S)
        self.assertIsNotNone(fn)
        self.assertIn("if (!icon) return;", fn.group(1))


if __name__ == "__main__":
    unittest.main()
