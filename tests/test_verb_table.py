"""
test_verb_table.py — sanity checks on the verb-table seed.

Run from repo root:
    python3 -m unittest tests.test_verb_table -v
"""
import pathlib
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
SEED = SKILL / "data" / "graph" / "verb_table_seed.yaml"


class VerbTableSeedTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(SEED.read_text(encoding="utf-8"))

    def test_yaml_parses(self):
        self.assertIsInstance(self.doc, dict)

    def test_has_inclusion_section(self):
        self.assertIn("inclusion", self.doc)
        self.assertGreaterEqual(len(self.doc["inclusion"]), 30,
                                "inclusion section should have ≥30 entries; saturation reached at v0.5")

    def test_has_borderline_section(self):
        self.assertIn("borderline", self.doc)
        self.assertGreaterEqual(len(self.doc["borderline"]), 5)

    def test_every_inclusion_entry_has_required_fields(self):
        for entry in self.doc["inclusion"]:
            self.assertIn("verb_forms", entry, f"missing verb_forms: {entry}")
            self.assertIn("edge_type", entry, f"missing edge_type: {entry}")
            self.assertIsInstance(entry["verb_forms"], list, f"verb_forms not a list: {entry['edge_type']}")
            self.assertGreater(len(entry["verb_forms"]), 0, f"empty verb_forms: {entry['edge_type']}")

    def test_every_inclusion_entry_has_lifetime(self):
        """Phase 2 requires lifetime annotation on every inclusion entry."""
        missing = [e["edge_type"] for e in self.doc["inclusion"] if "lifetime" not in e]
        self.assertEqual(missing, [], f"missing lifetime on: {missing}")

    def test_every_borderline_entry_has_lifetime(self):
        missing = [e["edge_type"] for e in self.doc["borderline"] if "lifetime" not in e]
        self.assertEqual(missing, [], f"missing lifetime on: {missing}")

    def test_lifetime_values_valid(self):
        valid = {"event", "state", "dispositional"}
        for entry in self.doc["inclusion"] + self.doc["borderline"]:
            lt = entry.get("lifetime")
            self.assertIn(lt, valid,
                          f"invalid lifetime '{lt}' on {entry.get('edge_type')}")

    def test_known_event_verbs_classified_event(self):
        """Spot-check: combat / speech / one-shot interaction verbs are events."""
        events = {"killed", "attacked", "told", "asked", "kissed", "gave"}
        for entry in self.doc["inclusion"]:
            if entry["edge_type"] in events:
                self.assertEqual(entry["lifetime"], "event",
                                 f"{entry['edge_type']} should be event, got {entry['lifetime']}")

    def test_known_state_verbs_classified_state(self):
        """Spot-check: ongoing-relationship verbs are states."""
        states = {"married_to", "parent_of", "member_of", "imprisoned",
                  "possessed_by", "swore_oath_to", "sworn_enemy_of"}
        for entry in self.doc["inclusion"]:
            if entry["edge_type"] in states:
                self.assertEqual(entry["lifetime"], "state",
                                 f"{entry['edge_type']} should be state, got {entry['lifetime']}")

    def test_known_dispositional_verbs_classified_dispositional(self):
        """Spot-check: drift-over-time verbs are dispositional."""
        dispositionals = {"fears", "wary_of", "knows", "in_love_with", "committed_to"}
        for entry in self.doc["inclusion"] + self.doc["borderline"]:
            if entry["edge_type"] in dispositionals:
                self.assertEqual(entry["lifetime"], "dispositional",
                                 f"{entry['edge_type']} should be dispositional, got {entry['lifetime']}")

    def test_category_object_ok_only_on_state_verbs(self):
        """category_object_ok only makes sense on state/dispositional verbs."""
        for entry in self.doc["inclusion"] + self.doc["borderline"]:
            if entry.get("category_object_ok"):
                self.assertIn(entry.get("lifetime"), {"state", "dispositional"},
                              f"category_object_ok=true on event verb {entry['edge_type']}")

    def test_patterns_have_template_and_emits(self):
        for entry in self.doc["inclusion"]:
            for pat in entry.get("patterns", []) or []:
                self.assertIn("template", pat, f"pattern missing template in {entry['edge_type']}")
                self.assertIn("emits", pat, f"pattern missing emits in {entry['edge_type']}")


if __name__ == "__main__":
    unittest.main()
