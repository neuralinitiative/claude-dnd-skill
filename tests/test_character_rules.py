"""Character rules the scripts apply instead of leaving to memory.

WHY
===
`character.py` covered creation arithmetic and level-up HP, and left the rest
of a character's rules to the model:

  * **2024 background increases.** In SRD 5.2 the ability increase comes from
    the background (+2/+1 or +1/+1/+1 among its three abilities), and `calc`
    could only apply 2014 racial bonuses.
  * **Hit Dice and long rests.** The long-rest procedure said "half max Hit
    Dice (round up)" -- the rule is half the total rounded DOWN, minimum 1, in
    2014 and every spent die in 2024 -- and it restored full HP to a
    character lying at 0 HP, who RAW gains nothing from the rest.
  * **The choices a level owes.** `levelup` printed HP and proficiency, so a
    fighter reaching 6 had no ASI prompted, a warlock reaching 2 no
    invocations, a paladin reaching 2 no fighting style.

The warlock column is pinned on its own: the 5e-database Levels file has
invocations known one higher at levels 4 and 6 than the SRD table, and copying
it would owe a warlock an invocation the rules do not grant.
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
DATASET_2024 = ROOT / "skills" / "dnd" / "data" / "dnd5e_srd_2024.json"
sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location("character_rules", SCRIPTS / "character.py")
character = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(character)


def cli(*args):
    return subprocess.run([sys.executable, str(SCRIPTS / "character.py"), *args],
                          capture_output=True, encoding="utf-8")


BASE = ["STR=15", "DEX=13", "CON=14", "INT=8", "WIS=12", "CHA=10"]


class BackgroundTests(unittest.TestCase):
    def test_plus_two_plus_one(self):
        self.assertEqual(character.parse_background_asi("STR=2,CON=1", "Soldier"),
                         {"STR": 2, "CON": 1})

    def test_plus_one_to_all_three(self):
        self.assertEqual(character.parse_background_asi("+1/+1/+1", "sage"),
                         {"CON": 1, "INT": 1, "WIS": 1})

    def test_an_ability_the_background_does_not_list_is_refused(self):
        with self.assertRaises(ValueError):
            character.parse_background_asi("WIS=2,CON=1", "soldier")

    def test_shapes_the_rules_do_not_allow_are_refused(self):
        for bad in ("STR=2,DEX=2", "STR=3", "STR=1", "STR=1,DEX=1", "STR=2,STR=1"):
            with self.assertRaises(ValueError, msg=bad):
                character.parse_background_asi(bad, "soldier")

    def test_cli_applies_and_prints_base_background_final(self):
        r = cli("calc", "--class", "fighter", "--background", "soldier",
                "--background-asi", "STR=2,CON=1", *BASE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"STR\s+15\s+\+2\s+17\s+\+3")
        self.assertRegex(r.stdout, r"CON\s+14\s+\+1\s+15\s+\+2")
        self.assertIn("HP (level 1): 12", r.stdout)

    def test_unknown_background_exits_nonzero(self):
        r = cli("calc", "--background", "noble", "--background-asi", "+1/+1/+1", *BASE)
        self.assertEqual(r.returncode, 2)
        self.assertIn("acolyte, criminal, sage, soldier", r.stderr)

    def test_missing_increase_is_reported_not_dropped(self):
        r = cli("calc", "--background", "acolyte", *BASE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Still to assign", r.stdout)

    def test_race_and_background_together_are_refused(self):
        self.assertEqual(cli("calc", "--race", "dwarf", "--background", "sage", *BASE).returncode, 2)

    def test_no_score_above_twenty(self):
        r = cli("calc", "--background", "soldier", "--background-asi", "STR=2,CON=1",
                "STR=19", "CON=14")
        self.assertEqual(r.returncode, 2)

    @unittest.skipUnless(DATASET_2024.is_file(), "2024 dataset not built (build_srd.py --ruleset 2024)")
    def test_table_matches_the_2024_dataset(self):
        data = json.loads(DATASET_2024.read_text(encoding="utf-8"))
        for rec in data.get("backgrounds", []):
            got = tuple(a["name"] for a in rec.get("ability_scores", []))
            self.assertEqual(character.BACKGROUND_ABILITIES[rec["index"]], got)


class HitDiceTests(unittest.TestCase):
    def test_each_die_heals_roll_plus_con(self):
        r = character.spend_hit_dice(10, 2, hp=5, max_hp=30, remaining=3, count=2, rolls=[4, 7])
        self.assertEqual(r["healed"], [6, 9])
        self.assertEqual(r["hp"], 20)
        self.assertEqual(r["remaining"], 1)

    def test_floor_is_zero_in_2014_and_one_in_2024(self):
        self.assertEqual(character.spend_hit_dice(6, -2, 5, 30, 1, rolls=[1])["healed"], [0])
        self.assertEqual(character.spend_hit_dice(6, -2, 5, 30, 1, ruleset="2024",
                                                  rolls=[1])["healed"], [1])

    def test_hp_stops_at_max(self):
        self.assertEqual(character.spend_hit_dice(10, 2, 28, 30, 1, rolls=[9])["hp"], 30)

    def test_refused_at_zero_hp_and_with_none_left(self):
        with self.assertRaises(ValueError):
            character.spend_hit_dice(10, 2, 0, 30, 3)
        with self.assertRaises(ValueError):
            character.spend_hit_dice(10, 2, 5, 30, 0)
        with self.assertRaises(ValueError):
            character.spend_hit_dice(10, 2, 5, 30, 1, count=2)

    def test_cli_refuses_with_nonzero_exit(self):
        r = cli("hitdie", "--die", "d10", "--con-mod", "2", "--hp", "0", "--max-hp", "20",
                "--remaining", "3")
        self.assertEqual(r.returncode, 2)


class LongRestTests(unittest.TestCase):
    def test_2014_regains_half_total_rounded_down(self):
        self.assertEqual(character.long_rest(5, 0, 9, 38)["hd_remaining"], 2)
        self.assertEqual(character.long_rest(7, 0, 9, 38)["hd_remaining"], 3)

    def test_2014_regains_at_least_one(self):
        self.assertEqual(character.long_rest(1, 0, 3, 10)["hd_remaining"], 1)

    def test_2024_regains_every_spent_die(self):
        self.assertEqual(character.long_rest(5, 1, 9, 38, ruleset="2024")["hd_remaining"], 5)

    def test_never_above_the_total(self):
        self.assertEqual(character.long_rest(4, 3, 9, 30)["hd_remaining"], 4)

    def test_full_hp_and_one_less_exhaustion(self):
        r = character.long_rest(5, 1, 9, 38, exhaustion=2)
        self.assertEqual((r["hp"], r["exhaustion"]), (38, 1))

    def test_nothing_for_a_character_at_zero_hp(self):
        r = character.long_rest(5, 1, 0, 38)
        self.assertFalse(r["benefit"])
        self.assertNotIn("hp", r)


class LevelUpChoiceTests(unittest.TestCase):
    def owed(self, cls, frm, rs="2014", sub=""):
        return " | ".join(character.owed_choices(cls, frm, frm + 1, rs, sub))

    def test_asi_levels_by_class(self):
        self.assertIn("Ability Score Improvement", self.owed("fighter", 5))
        self.assertIn("Ability Score Improvement", self.owed("rogue", 9))
        self.assertEqual(self.owed("wizard", 5), "")
        self.assertIn("Ability Score Improvement", self.owed("wizard", 7))

    def test_2024_level_19_is_an_epic_boon(self):
        self.assertIn("Epic Boon", self.owed("wizard", 18, "2024"))

    def test_subclass_level_by_ruleset(self):
        self.assertIn("subclass", self.owed("druid", 1, "2014"))
        self.assertNotIn("subclass", self.owed("druid", 1, "2024"))
        self.assertIn("subclass", self.owed("druid", 2, "2024"))

    def test_invocations_follow_the_srd_table_not_the_database(self):
        # SRD 5.1 Warlock: 2 known at levels 2-4, 3 at 5-6, 4 at 7-8.
        t = character.INVOCATIONS_KNOWN["2014"]
        self.assertEqual([t[i] for i in range(1, 9)], [0, 2, 2, 2, 3, 3, 4, 4])
        self.assertIn("learn 2 Eldritch Invocations", self.owed("warlock", 1))
        self.assertNotIn("Invocation", self.owed("warlock", 3))   # 4 owes none
        self.assertNotIn("Invocation", self.owed("warlock", 5))   # 6 owes none
        self.assertIn("learn 1 Eldritch Invocation", self.owed("warlock", 6))

    def test_2024_invocations(self):
        t = character.INVOCATIONS_KNOWN["2024"]
        self.assertEqual([t[i] for i in (1, 2, 5, 7, 9, 12, 15, 18)], [1, 3, 5, 6, 7, 8, 9, 10])

    def test_pact_boon_at_three_in_2014_only(self):
        self.assertIn("Pact Boon", self.owed("warlock", 2))
        self.assertNotIn("Pact Boon", self.owed("warlock", 2, "2024"))

    def test_fighting_styles(self):
        self.assertIn("Fighting Style", self.owed("paladin", 1))
        self.assertIn("Fighting Style", self.owed("ranger", 1))
        self.assertIn("Additional Fighting Style", self.owed("fighter", 9, sub="champion"))
        self.assertNotIn("Additional", self.owed("fighter", 9))
        self.assertIn("Additional Fighting Style", self.owed("fighter", 6, "2024", "champion"))

    def test_retroactive_hp(self):
        self.assertEqual(character.retro_hp(2, 3, 7), 7)

    def test_cli_lists_choices_and_srd_options(self):
        r = cli("levelup", "--class", "warlock", "--from", "1", "--con-mod", "3",
                "--con-mod-before", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("learn 2 Eldritch Invocations", r.stdout)
        self.assertIn("Agonizing Blast", r.stdout)
        self.assertIn("+1 max HP for the 1 earlier level", r.stdout)


class ClassOptionDataTests(unittest.TestCase):
    """The option records the level-up prompt names come from the dataset."""

    def test_dataset_carries_srd_options(self):
        data = json.loads(DATASET.read_text(encoding="utf-8"))
        opts = data["class_options"]
        kinds = {}
        for r in opts:
            kinds.setdefault(r["kind"], set()).add(r["name"])
        self.assertEqual(kinds["pact_boon"], {"Pact of the Chain", "Pact of the Blade",
                                              "Pact of the Tome"})
        self.assertIn("Agonizing Blast", kinds["invocation"])
        self.assertIn("Defense", kinds["fighting_style"])
        self.assertEqual(len(kinds["invocation"]), 32)


if __name__ == "__main__":
    unittest.main()
