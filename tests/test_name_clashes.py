"""The name registry notices a first-name echo, and reads names in every script.

WHY
===
Two gaps in `name_registry.py`:

  * **A shared first name passed the uniqueness check.** "Lady Willa Torne"
    is not a duplicate of the player character "Willa Finch", so `check`
    reported it unique -- and the table then had two people answering to
    "Willa", which is the confusion a player notices first, hearing their own
    name in an NPC's dialogue. The first name is now compared with titles
    stripped and reported as a warning.
  * **The prose scan was ASCII-only.** `_PROSE_NAME_PAT` was
    `[A-Z][a-z]+(?:\\s+[A-Z][a-z]+){1,2}`, so "Élodie Marchand", "Søren Halvik"
    and "Дмитрий Орлов" never matched. Words are now Unicode letters tested
    with str.isupper / str.islower; Chinese, Japanese and Korean, which have
    no capitals, are read by the honorific that follows a name.

The ASCII cases are asserted against the old regex directly, so the rewrite
cannot quietly change what it found in English.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location("name_registry_clash", SCRIPTS / "name_registry.py")
nr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nr)

OLD_PAT = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b")


class FirstNameTests(unittest.TestCase):
    def test_honorifics_are_stripped(self):
        self.assertEqual(nr.first_name("Lady Willa Torne"), "willa")
        self.assertEqual(nr.first_name("Dr. Mira Kell"), "mira")
        self.assertEqual(nr.first_name("Captain Sir Aldric Voss"), "aldric")

    def test_a_bare_title_is_its_own_name(self):
        self.assertEqual(nr.first_name("Captain"), "captain")

    def test_unspaced_cjk_name_is_not_guessed(self):
        self.assertIsNone(nr.first_name("田中太郎"))


class ClashTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"DND_CAMPAIGN_ROOT": self.td.name})
        self.env.start()
        nr.add("Willa Finch", "pc", "camp", 1)
        nr.add("Aldric Voss", "npc", "camp", 1)

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def test_npc_sharing_a_pc_first_name_is_flagged(self):
        r = nr.check("Lady Willa Torne")
        self.assertFalse(r["is_duplicate"])
        self.assertEqual(r["first_name_clash"], ["Willa Finch"])
        self.assertIn("first name matches", r["message"])

    def test_unrelated_name_is_clean(self):
        self.assertEqual(nr.check("Brannoc Hale")["first_name_clash"], [])

    def test_the_pc_does_not_clash_with_itself(self):
        self.assertEqual(nr.first_name_clashes("Willa Finch"), [])

    def test_clashes_lists_existing_npcs(self):
        nr.add("Sister Willa Marsh", "npc", "camp", 2)
        self.assertEqual(nr.all_first_name_clashes("camp"),
                         [("Sister Willa Marsh", ["Willa Finch"])])

    def test_cli_warns_and_clashes_exits_one(self):
        env = dict(os.environ)
        run = lambda *a: subprocess.run([sys.executable, str(SCRIPTS / "name_registry.py"), *a],
                                        capture_output=True, encoding="utf-8", env=env)
        r = run("check", "Lady Willa Torne")
        self.assertEqual(r.returncode, 0)
        self.assertIn("FIRST NAME", r.stdout)
        self.assertEqual(run("clashes").returncode, 0)
        nr.add("Lady Willa Torne", "npc", "camp", 2)
        self.assertEqual(run("clashes").returncode, 1)


class ProseScanTests(unittest.TestCase):
    def test_accented_cyrillic_and_greek_names_are_found(self):
        text = ("Élodie Marchand met Søren Halvik at the gate. "
                "Дмитрий Орлов waited with Νίκος Παππάς.")
        got = nr._prose_name_candidates(text)
        for name in ("Élodie Marchand", "Søren Halvik", "Дмитрий Орлов", "Νίκος Παππάς"):
            self.assertIn(name, got)

    def test_ascii_results_match_the_old_pattern(self):
        text = ("Aldric Voss crossed to the Pale Court. Then Mira Kell Dane Orrin spoke; "
                "A B. Ann Bee Cee Dee.\nTheo\nHarrow walked on.")
        self.assertEqual(nr._prose_name_candidates(text),
                         [m.group(1) for m in OLD_PAT.finditer(text)])

    def test_cjk_names_are_found_by_their_honorific(self):
        got = nr._prose_name_candidates("田中さんが来た。「李明先生，請坐。」 김민수씨가 왔다.")
        self.assertEqual(got, ["田中", "李明", "김민수"])

    def test_an_unspaced_chinese_run_is_not_cut_into_a_guess(self):
        # Nothing in 你看到李明先生 says where the name starts; no candidate
        # beats an invented one.
        self.assertEqual(nr._prose_name_candidates("你看到李明先生走進來"), [])

    def test_scan_reads_session_logs(self):
        with tempfile.TemporaryDirectory() as td:
            camp = pathlib.Path(td)
            (camp / "session-log.md").write_text(
                "Élodie Marchand opened the door. 田中さんが笑った。", encoding="utf-8")
            got = nr._scan_prose(camp, set())
            self.assertIn("Élodie Marchand", got)
            self.assertIn("田中", got)


if __name__ == "__main__":
    unittest.main()
