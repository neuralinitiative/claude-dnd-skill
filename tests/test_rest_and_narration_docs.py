"""The rest procedure states the rules, and the table guidance is where it is read.

WHY
===
`/dm:dnd rest long` told the DM to "restore all HP, half max Hit Dice (round
up)" and then run `tracker.py clear --all`. Each clause was wrong:

  * Hit Dice regained are half the character's total rounded DOWN, minimum 1
    (2014), or every spent die (2024) -- rounding up gave a level-1 or odd-
    level character an extra die every night;
  * a character at 0 HP gains nothing from a long rest -- RAW it needs at
    least 1 HP -- and "restore all HP" brought the dying back;
  * `clear --all` wiped exhaustion, petrification and the death saves of a
    character still lying at 0 HP, and the short rest's `clear` deleted
    every timed effect.

These are prose the model follows, so the guard is on the prose: the wrong
instruction must be gone and the right one present. The narration guidance
and the glossary are checked the same way, together with the state.md
sections they write to -- an instruction to write to a section the template
never creates is one the model has to improvise.
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "dnd"


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    nxt = text.find("\n## ", start + len(heading))
    return text[start: nxt if nxt != -1 else len(text)]


class RestProcedureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rest = _section((SKILL / "SKILL-commands.md").read_text(encoding="utf-8"),
                            "## `/dm:dnd rest <short|long>`")

    def test_hit_dice_rule_is_stated_correctly(self):
        self.assertIn("half the character's total, rounded down, minimum 1", self.rest)
        self.assertIn("all spent dice", self.rest)
        self.assertNotIn("round up", self.rest)

    def test_zero_hp_gate_comes_first(self):
        gate = self.rest.index("Life-state gate")
        self.assertLess(gate, self.rest.index("full HP"))
        self.assertIn("at least 1 HP", self.rest)

    def test_rests_no_longer_wipe_tracker_state(self):
        self.assertNotIn("clear --all", self.rest)
        self.assertNotIn("Clear encounter conditions", self.rest)
        self.assertNotIn("Clear all tracker state", self.rest)
        self.assertIn("do not run `tracker.py clear` for a rest", self.rest)

    def test_exhaustion_and_concentration(self):
        self.assertIn("one level lower", self.rest)
        self.assertIn("ends every concentration", self.rest)


class NarrationGuidanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        cls.state = (SKILL / "templates" / "state.md").read_text(encoding="utf-8")

    def test_each_rule_is_present(self):
        for phrase in ("Combat starts with hostile intent",
                       "is not a combatant",
                       "Call for a check only when failure would change the scene",
                       "Open each turn on what changed",
                       "write it to `state.md → ## DM Style Notes` immediately",
                       "replace any earlier note it contradicts",
                       "in the language it was asked",
                       "never say a sheet was updated unless you wrote it",
                       "check it isn't already recorded",
                       "One word per game term"):
            self.assertIn(phrase, self.skill, phrase)

    def test_the_sections_they_write_to_exist_in_the_template(self):
        for heading in ("## DM Style Notes", "## Glossary"):
            self.assertRegex(self.state, rf"(?m)^{re.escape(heading)}$", heading)

    def test_load_reads_the_glossary(self):
        load = (SKILL / "SKILL-commands.md").read_text(encoding="utf-8")
        self.assertIn("state.md contains `## Glossary`", load)


if __name__ == "__main__":
    unittest.main()
