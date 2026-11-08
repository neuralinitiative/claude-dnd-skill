"""Armor reads the way the rules state it, and the calculator uses it.

WHY
===
`build_srd.py` formatted every armor's `armor_class` from its `base` alone,
as `AC {base} + DEX`. Upstream carries `dex_bonus` and `max_bonus` beside it,
and both were ignored, so the bundled dataset said:

    Plate Armor   AC 18 + DEX      (heavy armor takes no DEX)
    Chain Shirt   AC 13 + DEX      (medium armor caps DEX at +2)
    Shield        AC 2 + DEX       (a shield is +2 on top of armor)

A DM reading `lookup.py equipment plate` was told a DEX 16 fighter in plate
has AC 21. Heavy armor now drops DEX, medium carries `(max 2)`, a shield
reads `AC +2`.

`character.py calc --armor` reads those records back, so a wrong string would
produce a wrong AC rather than only a wrong line of text. The calculator tests
use a 16 DEX on purpose: it is the score at which every one of the three
mistakes shows.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "dnd" / "scripts"
DATASET = ROOT / "skills" / "dnd" / "data" / "dnd5e_srd.json"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build = _load("build_srd_armor", "build_srd.py")
character = _load("character_armor", "character.py")

#: What the SRD's armor table says, by index.
EXPECTED = {
    "padded-armor":          "AC 11 + DEX",
    "leather-armor":         "AC 11 + DEX",
    "studded-leather-armor": "AC 12 + DEX",
    "hide-armor":            "AC 12 + DEX (max 2)",
    "chain-shirt":           "AC 13 + DEX (max 2)",
    "scale-mail":            "AC 14 + DEX (max 2)",
    "breastplate":           "AC 14 + DEX (max 2)",
    "half-plate-armor":      "AC 15 + DEX (max 2)",
    "ring-mail":             "AC 14",
    "chain-mail":            "AC 16",
    "splint-armor":          "AC 17",
    "plate-armor":           "AC 18",
    "shield":                "AC +2",
}


class FormatterTests(unittest.TestCase):
    """The formatter, on records shaped like upstream's 2014 and 2024 files."""

    def fmt(self, **rec):
        return build._norm_equipment(rec)["ac"]

    def test_heavy_armor_takes_no_dex(self):
        self.assertEqual(self.fmt(index="plate-armor", armor_category="Heavy",
                                  armor_class={"base": 18, "dex_bonus": False}), "AC 18")

    def test_medium_armor_caps_dex(self):
        self.assertEqual(self.fmt(index="chain-shirt", armor_category="Medium",
                                  armor_class={"base": 13, "dex_bonus": True, "max_bonus": 2}),
                         "AC 13 + DEX (max 2)")

    def test_light_armor_adds_all_dex(self):
        self.assertEqual(self.fmt(index="leather-armor", armor_category="Light",
                                  armor_class={"base": 11, "dex_bonus": True}), "AC 11 + DEX")

    def test_shield_is_a_bonus_not_a_base(self):
        self.assertEqual(self.fmt(index="shield", armor_category="Shield",
                                  armor_class={"base": 2, "dex_bonus": False}), "AC +2")

    def test_2024_shape_without_a_category(self):
        # The 2024 file has no armor_category and writes max_bonus: 0 on
        # heavy armor; neither may bring the DEX back.
        self.assertEqual(self.fmt(index="chain-mail",
                                  armor_class={"base": 16, "dex_bonus": False, "max_bonus": 0}),
                         "AC 16")
        self.assertEqual(self.fmt(index="shield", armor_class={"base": 2, "dex_bonus": False}),
                         "AC +2")

    def test_non_armor_has_no_ac(self):
        self.assertEqual(self.fmt(index="dagger"), "")


class DatasetTests(unittest.TestCase):
    """The committed dataset carries the corrected strings, and only those."""

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(DATASET.read_text(encoding="utf-8"))

    def test_every_armor_record_matches_the_srd_table(self):
        got = {r["index"]: r["ac"] for r in self.data["equipment"] if r.get("ac")}
        self.assertEqual(got, EXPECTED)

    def test_no_record_still_says_plus_dex_on_heavy_armor(self):
        bad = [r["name"] for r in self.data["equipment"]
               if r.get("ac") and r.get("str_minimum") and "DEX" in r["ac"]]
        self.assertEqual(bad, [])

    def test_record_counts_are_consistent(self):
        meta = self.data["_meta"]
        counts = {k: len(v) for k, v in self.data.items() if k != "_meta"}
        self.assertEqual(meta["record_counts"], counts)
        self.assertEqual(meta["total_records"], sum(counts.values()))
        self.assertEqual(counts["equipment"], 237)


class ArmorClassTests(unittest.TestCase):
    """`character.py calc --armor` — the calculator built on those records."""

    SCORES = {"STR": 15, "DEX": 16, "CON": 14, "INT": 10, "WIS": 14, "CHA": 10}

    def ac(self, cls="fighter", armor="none", shield=False, style=""):
        return character.armor_class(self.SCORES, cls, armor, shield, style)[0]

    def test_heavy(self):
        self.assertEqual(self.ac(armor="plate-armor"), 18)

    def test_medium_caps_dex_at_two(self):
        self.assertEqual(self.ac(armor="chain-shirt"), 15)

    def test_light_takes_full_dex(self):
        self.assertEqual(self.ac(armor="studded-leather-armor"), 15)

    def test_shield_adds_two(self):
        self.assertEqual(self.ac(armor="chain-mail", shield=True), 18)

    def test_defense_style_only_while_armored(self):
        self.assertEqual(self.ac(armor="chain-mail", style="defense"), 17)
        self.assertEqual(self.ac(armor="none", style="defense"), 13)

    def test_unarmored(self):
        self.assertEqual(self.ac(), 13)

    def test_barbarian_unarmored_defense_adds_con_and_keeps_shield(self):
        self.assertEqual(self.ac(cls="barbarian"), 15)
        self.assertEqual(self.ac(cls="barbarian", shield=True), 17)

    def test_monk_unarmored_defense_adds_wis_but_not_with_a_shield(self):
        self.assertEqual(self.ac(cls="monk"), 15)
        self.assertEqual(self.ac(cls="monk", shield=True), 15)   # 10 + 3 + shield 2

    def test_unknown_armor_raises(self):
        with self.assertRaises(ValueError):
            self.ac(armor="mithral-plate")

    def test_cli_prints_ac_and_exits_nonzero_on_unknown_armor(self):
        ok = subprocess.run(
            [sys.executable, str(SCRIPTS / "character.py"), "calc", "--class", "fighter",
             "--armor", "plate-armor", "--shield", "STR=15", "DEX=16"],
            capture_output=True, encoding="utf-8")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("Armor Class: 20", ok.stdout)
        bad = subprocess.run(
            [sys.executable, str(SCRIPTS / "character.py"), "calc", "--armor", "nope"],
            capture_output=True, encoding="utf-8")
        self.assertEqual(bad.returncode, 2)


if __name__ == "__main__":
    unittest.main()
