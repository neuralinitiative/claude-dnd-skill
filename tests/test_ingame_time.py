"""Timed effects run on in-game time, and a rest no longer wipes them.

WHY
===
`tracker.py` started minute and hour effects with `started_at=time.time()`
and expired them on the real clock, and both rest procedures ran
`tracker.py clear`, which set `effects=[]`. Together:

  * a one-hour short rest deleted an eight-hour Mage Armor outright;
  * an in-game eight-hour rest expired nothing, because eight hours had not
    passed at the table;
  * a long rest ran `clear --all`, clearing exhaustion and resetting the death
    saves of a character still lying at 0 HP.

Durations are now counted in in-game minutes. They move when a turn ticks,
when `effect advance` runs, and when `calendar.py` advances or rests. `clear`
ends what an encounter owns and keeps the rest.

Also here: typed modifiers on an effect (Bless: +1d4 to attacks and saves),
which `dice.py` and `combat.py` read through `active_modifiers`.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location("tracker", SCRIPTS / "tracker.py")
tracker = importlib.util.module_from_spec(_spec)
sys.modules["tracker"] = tracker
_spec.loader.exec_module(tracker)

CAMP = "unittest-ingame-time"


class _Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.td.name)
        (self.root / "campaigns" / CAMP).mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"DND_CAMPAIGN_ROOT": str(self.root)})
        self.env.start()
        # The display is not running; keep the tracker from spawning it.
        self.quiet = [mock.patch.object(tracker, "_push_conditions", lambda *a, **k: None),
                      mock.patch.object(tracker, "_send_announce", lambda *a, **k: None)]
        for q in self.quiet:
            q.start()
        self.out = mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO())
        self.stdout = self.out.start()

    def tearDown(self):
        self.out.stop()
        for q in self.quiet:
            q.stop()
        self.env.stop()
        self.td.cleanup()

    def state(self) -> dict:
        return json.loads((self.root / "campaigns" / CAMP / "tracker.json").read_text(encoding="utf-8"))

    def effects(self, who="mara") -> list:
        return [e["name"] for e in self.state()[who]["effects"]]

    def start(self, who, name, dur, conc=False, mod="", on=""):
        tracker.cmd_effect(CAMP, "start", who, spell=name, duration=dur, is_conc=conc,
                           mod=mod, on=on)


class RestTests(_Base):
    def test_short_rest_keeps_an_eight_hour_effect(self):
        self.start("Mara", "Mage Armor", "8h")
        tracker.cmd_rest(CAMP, "short")
        self.assertEqual(self.effects(), ["Mage Armor"])
        self.assertAlmostEqual(self.state()["mara"]["effects"][0]["remaining_minutes"], 420)

    def test_long_rest_spends_an_eight_hour_effect(self):
        self.start("Mara", "Mage Armor", "8h")
        tracker.cmd_rest(CAMP, "long")
        self.assertEqual(self.effects(), [])

    def test_wall_clock_time_does_not_expire_anything(self):
        self.start("Mara", "Mage Armor", "1h")
        real = time.time
        with mock.patch("time.time", lambda: real() + 10 * 3600):
            tracker.cmd_effect(CAMP, "tick", "Mara")
            tracker.cmd_status(CAMP)
        self.assertEqual(self.effects(), ["Mage Armor"])

    def test_long_rest_ends_concentration_and_lowers_exhaustion(self):
        # 24 hours, so the effect running out cannot be what ends it.
        self.start("Mara", "Hex", "24h", conc=True)
        tracker.cmd_condition(CAMP, "Mara", "add", "exhausted")
        tracker.cmd_condition(CAMP, "Mara", "add", "exhausted")
        tracker.cmd_rest(CAMP, "long")
        st = self.state()["mara"]
        self.assertIsNone(st["concentration"])
        self.assertEqual(self.effects(), [])
        self.assertEqual(st["exhaustion"], 1)
        self.assertIn("exhausted", st["conditions"])

    def test_long_rest_gives_nothing_to_a_character_at_zero_hp(self):
        tracker.cmd_condition(CAMP, "Theo", "add", "exhausted")
        tracker.cmd_saves(CAMP, "Theo", "failure")
        tracker.cmd_rest(CAMP, "long")
        st = self.state()["theo"]
        self.assertEqual(st["exhaustion"], 1)
        self.assertEqual(st["death_saves"]["failures"], 1)
        self.assertIn("at 0 HP", self.stdout.getvalue())


class ClearTests(_Base):
    def test_clear_keeps_minute_and_hour_effects_and_drops_round_effects(self):
        self.start("Mara", "Mage Armor", "8h")
        self.start("Mara", "Bless", "10r", conc=True)
        tracker.cmd_clear(CAMP)
        st = self.state()["mara"]
        self.assertEqual(self.effects(), ["Mage Armor"])
        self.assertIsNone(st["concentration"])

    def test_clear_keeps_concentration_held_by_a_surviving_effect(self):
        self.start("Mara", "Fly", "10m", conc=True)
        tracker.cmd_clear(CAMP)
        self.assertEqual(self.state()["mara"]["concentration"], "Fly")

    def test_clear_keeps_lasting_conditions(self):
        tracker.cmd_condition(CAMP, "Mara", "add", "exhausted")
        tracker.cmd_condition(CAMP, "Mara", "add", "prone")
        tracker.cmd_condition(CAMP, "Mara", "add", "petrified")
        tracker.cmd_clear(CAMP, clear_all=True)
        self.assertEqual(sorted(self.state()["mara"]["conditions"]), ["exhausted", "petrified"])

    def test_clear_all_never_resets_a_dead_characters_saves(self):
        for _ in range(3):
            tracker.cmd_saves(CAMP, "Theo", "failure")
        tracker.cmd_saves(CAMP, "Mara", "failure")
        tracker.cmd_clear(CAMP, clear_all=True)
        self.assertEqual(self.state()["theo"]["death_saves"]["failures"], 3)
        self.assertEqual(self.state()["mara"]["death_saves"]["failures"], 0)


class AdvanceTests(_Base):
    def test_advance_runs_minutes_and_rounds_down_together(self):
        self.start("Mara", "Bless", "10r", conc=True)
        self.start("Mara", "Disguise Self", "1h")
        tracker.cmd_effect(CAMP, "advance", "30m")
        self.assertEqual(self.effects(), ["Disguise Self"])
        self.assertIsNone(self.state()["mara"]["concentration"])
        tracker.cmd_effect(CAMP, "advance", "30")
        self.assertEqual(self.effects(), [])

    def test_advance_parses_hours_and_rounds(self):
        self.assertEqual(tracker.parse_time("8h"), 480)
        self.assertEqual(tracker.parse_time("10r"), 1.0)
        self.assertEqual(tracker.parse_time("90"), 90)
        self.assertIsNone(tracker.parse_time("soon"))

    def test_tick_is_one_round_of_in_game_time(self):
        self.start("Mara", "Shield of Faith", "1m")
        for _ in range(9):
            tracker.cmd_effect(CAMP, "tick", "Mara")
        self.assertEqual(self.effects(), ["Shield of Faith"])
        tracker.cmd_effect(CAMP, "tick", "Mara")
        self.assertEqual(self.effects(), [])

    def test_exhaustion_stacks_in_levels(self):
        for _ in range(3):
            tracker.cmd_condition(CAMP, "Mara", "add", "exhausted")
        tracker.cmd_condition(CAMP, "Mara", "remove", "exhausted")
        self.assertEqual(self.state()["mara"]["exhaustion"], 2)


class MigrationTests(_Base):
    def test_an_old_wall_clock_effect_keeps_the_time_it_showed(self):
        path = self.root / "campaigns" / CAMP / "tracker.json"
        path.write_text(json.dumps({"mara": {
            "name": "Mara", "conditions": [], "concentration": None,
            "death_saves": {"successes": 0, "failures": 0, "stable": False},
            "effects": [{"name": "Mage Armor", "concentration": False,
                         "duration_type": "hours", "duration_seconds": 8 * 3600,
                         "started_at": time.time() - 3600}]}}), encoding="utf-8")
        tracker.cmd_rest(CAMP, "short")
        eff = self.state()["mara"]["effects"][0]
        self.assertNotIn("started_at", eff)
        self.assertAlmostEqual(eff["remaining_minutes"], 360, delta=1)


class ModifierTests(_Base):
    def test_bless_reaches_attack_and_save_but_not_checks(self):
        self.start("Mara", "Bless", "10r", conc=True, mod="+1d4", on="attack,save")
        self.assertEqual(tracker.active_modifiers(CAMP, "Mara", "attack"), [("Bless", "+1d4")])
        self.assertEqual(tracker.active_modifiers(CAMP, "mara", "save"), [("Bless", "+1d4")])
        self.assertEqual(tracker.active_modifiers(CAMP, "Mara", "check"), [])

    def test_modifier_ends_with_its_effect(self):
        self.start("Mara", "Bless", "1r", mod="+1d4", on="attack")
        tracker.cmd_effect(CAMP, "tick", "Mara")
        self.assertEqual(tracker.active_modifiers(CAMP, "Mara", "attack"), [])

    def test_status_shows_the_modifier(self):
        self.start("Mara", "Bane", "10r", mod="-1d4", on="attack,save")
        self.stdout.seek(0)
        self.stdout.truncate()          # the start line prints it too
        tracker.cmd_status(CAMP)
        self.assertIn("-1d4 attack/save", self.stdout.getvalue())

    def test_bad_modifier_is_refused(self):
        with self.assertRaises(SystemExit):
            self.start("Mara", "Bless", "10r", mod="+1d4", on="initiative")
        with self.assertRaises(SystemExit):
            self.start("Mara", "Bless", "10r", mod="lots", on="attack")


class CalendarDrivesTheTrackerTests(unittest.TestCase):
    """calendar.py rest / advance move the effects by the same in-game time."""

    def test_calendar_rest_and_advance(self):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, DND_CAMPAIGN_ROOT=td)
            (pathlib.Path(td) / "campaigns" / CAMP).mkdir(parents=True)

            def run(script, *args):
                r = subprocess.run([sys.executable, str(SCRIPTS / script), "-c", CAMP, *args],
                                   capture_output=True, encoding="utf-8", env=env)
                self.assertEqual(r.returncode, 0, r.stderr)
                return r.stdout

            run("tracker.py", "effect", "start", "Mara", "Mage Armor", "8h")
            run("calendar.py", "init")
            run("calendar.py", "rest", "short")
            state = pathlib.Path(td) / "campaigns" / CAMP / "tracker.json"
            eff = json.loads(state.read_text(encoding="utf-8"))["mara"]["effects"]
            self.assertEqual([e["name"] for e in eff], ["Mage Armor"])
            out = run("calendar.py", "advance", "7", "hours")
            self.assertIn("Mage Armor EXPIRED", out)


if __name__ == "__main__":
    unittest.main()
