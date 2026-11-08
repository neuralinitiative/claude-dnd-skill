"""Rolls are labelled by what they are for, and the combat helpers obey the rules.

WHY
===
Four defects in the dice path, each reproduced against this repo:

  * **Every natural 20 was a CRITICAL HIT.** `dice.py` labelled any nat 20
    CRITICAL HIT and any nat 1 FUMBLE, so a Perception check or a saving throw
    announced a crit -- and the display flashes its full-screen crit overlay
    on exactly that text. Only an attack roll crits. `--vs` now says what the
    roll is for; without it nothing is called a crit.
  * **`combat.py attack --crit` did nothing.** `resolve_attack` accepted
    `is_crit` and never read it, so an attack on a paralyzed or unconscious
    target within 5 feet rolled ordinary damage.
  * **Topple saved with the attacker's modifier.** The target's CON save added
    the ATTACKER's ability mod, and the DC assumed +2 proficiency -- a strong
    attacker made its own target harder to knock down, and a level-5 fighter's
    DC was one short.
  * **Two random sources.** `dice.py`, `combat.py`, the dice server and
    `ability-scores.py` used `random` while `dice_player.py` and the display
    used `secrets`.

Randomness is pinned with `secrets.randbelow` patched, so each test chooses
the d20 face it needs instead of hoping for it.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dice = _load("dice_rules", SCRIPTS / "dice.py")
combat = _load("combat_rules", SCRIPTS / "combat.py")


def faces(*values):
    """Patch secrets.randbelow so successive dice show `values` (1-based faces)."""
    seq = iter(values)
    return mock.patch("secrets.randbelow", lambda n: next(seq) - 1)


def run_dice(notation, faces_, **kw) -> str:
    buf = io.StringIO()
    with faces(*faces_), contextlib.redirect_stdout(buf):
        dice.run(notation, force_local=True, **kw)
    return buf.getvalue()


class D20LabelTests(unittest.TestCase):
    def test_a_check_never_announces_a_crit(self):
        for vs in (None, "check", "save"):
            out = run_dice("d20+3", [20], vs=vs)
            self.assertNotIn("CRITICAL", out, vs)
            self.assertIn("natural 20", out)
            out = run_dice("d20+3", [1], vs=vs)
            self.assertNotIn("FUMBLE", out, vs)

    def test_an_attack_crits_and_fumbles(self):
        self.assertIn("CRITICAL HIT", run_dice("d20+3", [20], vs="attack"))
        self.assertIn("FUMBLE", run_dice("d20+3", [1], vs="attack"))

    def test_a_death_save_reports_its_own_rules(self):
        self.assertIn("regains 1 HP", run_dice("d20", [20], vs="death"))
        self.assertIn("TWO failures", run_dice("d20", [1], vs="death"))

    def test_advantage_labels_the_die_it_kept(self):
        out = run_dice("d20+2 adv", [20, 4], vs="attack")
        self.assertIn("CRITICAL HIT", out)
        out = run_dice("d20+2 adv", [20, 4], vs="save")
        self.assertNotIn("CRITICAL", out)

    def test_dc_verdict_is_arithmetic(self):
        self.assertIn("PASS (vs DC 13)", run_dice("d20+3", [10], vs="save", dc=13))
        self.assertIn("FAIL (vs DC 14)", run_dice("d20+3", [10], vs="save", dc=14))

    def test_a_nat_20_hits_any_ac_and_a_nat_1_misses_any(self):
        self.assertIn("PASS (vs AC 30)", run_dice("d20+3", [20], vs="attack", dc=30))
        self.assertIn("FAIL (vs AC 2)", run_dice("d20+9", [1], vs="attack", dc=2))

    def test_a_check_has_no_automatic_success(self):
        self.assertIn("FAIL (vs DC 25)", run_dice("d20+3", [20], vs="check", dc=25))

    def test_effects_are_added_before_the_verdict(self):
        out = run_dice("d20+3", [10, 4], vs="attack", dc=17,
                       extras=[("Bless", "+1d4")])
        self.assertIn("Bless +1d4", out)
        self.assertIn("Total: 17", out)
        self.assertIn("PASS (vs AC 17)", out)

    def test_silent_returns_the_total_with_effects(self):
        with faces(10, 4), contextlib.redirect_stdout(io.StringIO()):
            total = dice.run("d20+3", silent=True, force_local=True, vs="attack",
                             extras=[("Bless", "+1d4")])
        self.assertEqual(total, 17)


class CombatCritTests(unittest.TestCase):
    def test_crit_flag_makes_a_hit_a_critical_hit(self):
        with faces(12, 5, 6):
            r = combat.resolve_attack(5, 15, "1d8+3", is_crit=True)
        self.assertTrue(r["hit"])
        self.assertTrue(r["crit"])
        self.assertEqual(r["damage_rolls"], [5, 6])
        self.assertEqual(r["damage"], 14)

    def test_crit_flag_does_not_turn_a_miss_into_a_hit(self):
        with faces(3):
            r = combat.resolve_attack(5, 15, "1d8+3", is_crit=True)
        self.assertFalse(r["hit"])
        self.assertFalse(r["crit"])

    def test_a_natural_one_still_misses_a_helpless_target(self):
        with faces(1):
            r = combat.resolve_attack(20, 5, "1d8+3", is_crit=True)
        self.assertFalse(r["hit"])

    def test_without_the_flag_an_ordinary_hit_is_not_a_crit(self):
        with faces(12, 5):
            r = combat.resolve_attack(5, 15, "1d8+3")
        self.assertFalse(r["crit"])
        self.assertEqual(r["damage"], 8)

    def test_attack_modifiers_from_effects(self):
        with faces(10, 3, 4):
            r = combat.resolve_attack(4, 17, "1d6+2", attack_mods=[("Bless", "+1d4")])
        self.assertEqual(r["total"], 17)
        self.assertTrue(r["hit"])
        self.assertIn("Bless", combat.format_attack(r))


class ToppleTests(unittest.TestCase):
    def test_save_uses_the_targets_bonus_not_the_attackers(self):
        with faces(10):
            out = combat.apply_mastery("topple", True, ability_mod=4, prof=3, target_save=-1)
        self.assertEqual(out["save_dc"], 15)          # 8 + 4 + 3
        self.assertEqual(out["save_total"], 9)        # d20(10) - 1, not + 4
        self.assertFalse(out["save_passed"])

    def test_proficiency_is_not_assumed(self):
        out = combat.apply_mastery("topple", True, ability_mod=4)
        self.assertNotIn("save_dc", out)
        self.assertNotIn("save_roll", out)
        self.assertIn("--prof", out["text"])

    def test_no_roll_without_the_targets_save_bonus(self):
        out = combat.apply_mastery("topple", True, ability_mod=3, prof=2)
        self.assertEqual(out["save_dc"], 13)
        self.assertNotIn("save_roll", out)

    def test_explicit_dc_still_wins(self):
        with faces(14):
            out = combat.apply_mastery("topple", True, ability_mod=3, prof=2,
                                       save_dc=16, target_save=2)
        self.assertEqual(out["save_dc"], 16)
        self.assertTrue(out["save_passed"])


class OneRandomSourceTests(unittest.TestCase):
    """Game dice come from `secrets` everywhere a die is rolled."""

    FILES = [SCRIPTS / "dice.py", SCRIPTS / "combat.py", SCRIPTS / "ability-scores.py",
             ROOT / "dice-server" / "server.py", SCRIPTS / "dice_player.py"]

    def test_no_game_dice_use_random(self):
        for f in self.FILES:
            code = re.sub(r"#.*", "", f.read_text(encoding="utf-8"))
            self.assertNotRegex(code, r"(?m)^\s*(import|from)\s+random\b", f.name)
            self.assertNotIn("random.randint", code, f.name)
            self.assertIn("secrets", code, f.name)


class EffectsCliTests(unittest.TestCase):
    """`dice.py --effects` reads the tracker end to end."""

    def test_dice_cli_adds_bless(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, DND_CAMPAIGN_ROOT=td)
            (pathlib.Path(td) / "campaigns" / "fx").mkdir(parents=True)
            subprocess.run([sys.executable, str(SCRIPTS / "tracker.py"), "-c", "fx", "effect",
                            "start", "Mara", "Bless", "10r", "conc", "--mod", "+1d4",
                            "--on", "attack,save"], env=env, capture_output=True, check=True)
            r = subprocess.run([sys.executable, str(SCRIPTS / "dice.py"), "d20+5", "--vs",
                                "save", "--effects", "Mara", "-c", "fx", "--auto"],
                               env=env, capture_output=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Bless +1d4", r.stdout)
            r = subprocess.run([sys.executable, str(SCRIPTS / "dice.py"), "d20+5", "--vs",
                                "check", "--effects", "Mara", "-c", "fx", "--auto"],
                               env=env, capture_output=True, encoding="utf-8")
            self.assertNotIn("Bless", r.stdout)


if __name__ == "__main__":
    unittest.main()
