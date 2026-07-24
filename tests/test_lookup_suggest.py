"""
test_lookup_suggest.py — near-miss "did you mean?" suggestions for lookup.py.

Runs against the bundled dnd5e_srd.json (no fixtures needed): a mistyped rule,
condition, spell, or monster should surface the closest real name instead of
dead-ending.

Run from repo root:
    python3 -m unittest tests.test_lookup_suggest -v
"""
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "scripts"))

import lookup  # noqa: E402


def _names(hints):
    return [nm.lower() for nm, _cat in hints]


class SuggestTests(unittest.TestCase):

    def test_condition_typo(self):
        hints = lookup.suggest("poisonned", category="condition")
        self.assertIn("poisoned", _names(hints))

    def test_spell_typo(self):
        hints = lookup.suggest("fireballl", category="spell")
        self.assertIn("fireball", _names(hints))

    def test_monster_typo(self):
        hints = lookup.suggest("gobblin", category="monster")
        self.assertIn("goblin", _names(hints))

    def test_feature_typo(self):
        hints = lookup.suggest("cunnning action", category="feature")
        self.assertIn("cunning action", _names(hints))

    def test_cross_category_typo(self):
        # No category given — should still find the near-miss across categories.
        hints = lookup.suggest("poisonned")
        self.assertIn("poisoned", _names(hints))

    def test_respects_result_cap(self):
        hints = lookup.suggest("fireballl", category="spell", n=2)
        self.assertLessEqual(len(hints), 2)

    def test_exact_name_still_offered_when_query_is_garbage(self):
        # A totally unrelated query returns few/no suggestions, never raises.
        hints = lookup.suggest("zzzxqqywv", category="condition")
        self.assertIsInstance(hints, list)

    def test_category_scoping(self):
        # A condition typo scoped to spells must not return the condition.
        hints = lookup.suggest("poisonned", category="spell")
        self.assertNotIn("poisoned", _names(hints))

    def test_returns_name_category_tuples(self):
        hints = lookup.suggest("fireballl", category="spell")
        self.assertTrue(hints)
        nm, cat = hints[0]
        self.assertIsInstance(nm, str)
        self.assertEqual(cat, "spells")


if __name__ == "__main__":
    unittest.main()
