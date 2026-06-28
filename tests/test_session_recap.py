"""
test_session_recap.py — sheet parsing + state-diff for the recap tool.

Run from repo root:
    python3 -m unittest tests.test_session_recap -v
"""
import pathlib
import sys
import textwrap
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "scripts"))

import session_recap as sr  # noqa: E402


SHEET = textwrap.dedent("""\
    # Aldric

    ## Identity
    - **Race:** Human | **Class:** Fighter | **Level:** 3 | **Background:** Soldier

    ## Combat Stats
    - **HP:** 18 / 30 | **Temp HP:** 5
    - **AC:** 16 | **Initiative:** +2 | **Speed:** 30
    - **Hit Dice:** 3d10 (remaining: 1)
    - **Death Saves:** Successes: 1 | Failures: 2
    - **Exhaustion:** 1
    - **Inspiration:** yes
    - **Conditions:** poisoned, prone
    - **Concentration:** Bless

    ## Spell Slots (if applicable)
    | Level | Total | Used |
    |-------|-------|------|
    | 1st | 4 | 2 |
    | 2nd | 2 | 0 |
""")


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.snap = sr.parse_character_sheet(SHEET)

    def test_basic_fields(self):
        self.assertEqual(self.snap["name"], "Aldric")
        self.assertEqual(self.snap["level"], 3)
        self.assertEqual(self.snap["current_hp"], 18)
        self.assertEqual(self.snap["max_hp"], 30)
        self.assertEqual(self.snap["temp_hp"], 5)
        self.assertEqual(self.snap["exhaustion"], 1)
        self.assertTrue(self.snap["inspiration"])

    def test_hit_dice_and_death_saves(self):
        self.assertEqual(self.snap["hit_dice_remaining"], {"d10": 1})
        self.assertEqual(self.snap["death_saves"],
                         {"successes": 1, "failures": 2})

    def test_conditions_and_concentration(self):
        self.assertEqual(set(self.snap["conditions"]), {"poisoned", "prone"})
        self.assertEqual(self.snap["concentration"], "Bless")

    def test_spell_slots_records_only_used(self):
        # 1st has 2 used; 2nd has 0 used → omitted.
        self.assertEqual(self.snap["spell_slots_expended"], {"1": 2})

    def test_blank_sheet_defaults(self):
        snap = sr.parse_character_sheet("# Mara\n")
        self.assertEqual(snap["name"], "Mara")
        self.assertIsNone(snap["current_hp"])
        self.assertEqual(snap["temp_hp"], 0)
        self.assertEqual(snap["conditions"], [])


class DiffTests(unittest.TestCase):
    def _diff(self, before, after):
        return sr.diff_character(before, after)

    def test_damage_phrase(self):
        b = sr.parse_character_sheet("# Aldric\n## Combat Stats\n- **HP:** 30 / 30\n")
        a = sr.parse_character_sheet("# Aldric\n## Combat Stats\n- **HP:** 18 / 30\n")
        changes = self._diff(b, a)
        summary = sr.render_summary(changes)
        self.assertIn("took 12 damage", summary)
        self.assertIn("30→18 HP", summary)

    def test_condition_and_slot_and_level(self):
        b = sr.parse_character_sheet(
            "# Aldric\n## Combat Stats\n- **HP:** 30 / 30\n"
            "- **Conditions:** none\n"
            "## Spell Slots\n| Level | Total | Used |\n| 1st | 4 | 0 |\n"
        )
        a = sr.parse_character_sheet(
            "# Aldric\n## Identity\n- **Level:** 4\n## Combat Stats\n- **HP:** 30 / 30\n"
            "- **Conditions:** poisoned\n"
            "## Spell Slots\n| Level | Total | Used |\n| 1st | 4 | 2 |\n"
        )
        summary = sr.render_summary(self._diff(b, a))
        self.assertIn("gained Poisoned", summary)
        self.assertIn("spent 2 level 1 slots", summary)
        self.assertIn("levelled up to 4", summary)

    def test_no_change_is_empty(self):
        b = sr.parse_character_sheet(SHEET)
        a = sr.parse_character_sheet(SHEET)
        self.assertEqual(self._diff(b, a), [])
        self.assertEqual(sr.render_summary([]), "")


class PartyTests(unittest.TestCase):
    def test_party_diff_matches_by_name(self):
        before = {"characters": {
            "Aldric": sr.parse_character_sheet("# Aldric\n## Combat Stats\n- **HP:** 30 / 30\n"),
            "Mara": sr.parse_character_sheet("# Mara\n## Combat Stats\n- **HP:** 20 / 20\n"),
        }}
        after = {"characters": {
            "Aldric": sr.parse_character_sheet("# Aldric\n## Combat Stats\n- **HP:** 25 / 30\n"),
            "Mara": sr.parse_character_sheet("# Mara\n## Combat Stats\n- **HP:** 20 / 20\n"),
        }}
        summary = sr.render_summary(sr.diff_party(before, after))
        self.assertIn("Aldric:", summary)
        self.assertNotIn("Mara:", summary)


if __name__ == "__main__":
    unittest.main()
