"""An award that never happened has to be findable.

WHY
===
XP is awarded when the GM remembers to run `xp.py award`. When a fight resolved
and nobody ran it, nothing was written, nothing was raised, and the only way it
surfaced was a player noticing weeks later that their total had not moved — by
which point the encounters that should have fed it are unreconstructable.

The award also left no trace beyond a number on a sheet, so there was nothing to
compare a sheet against. The ledger is that record. `xp.py check` reconciles it.

WHAT THIS IS NOT
================
It does not fill gaps automatically. Doing that needs to know which encounters
happened, and nothing in the campaign format records that yet — `## Active
Combat` is free prose. Inventing a structured encounter id would change the
state format for every existing campaign, which is a decision to take
deliberately rather than as a side effect of a bug fix.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "dnd" / "scripts"
# xp.py imports sibling modules, so the scripts dir must be importable first.
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("xp", SCRIPTS / "xp.py")
xp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(xp)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._d.name)
        self._orig = xp.CAMPAIGNS_DIR
        xp.CAMPAIGNS_DIR = self.base
        (self.base / "c" / "characters").mkdir(parents=True)

    def tearDown(self):
        xp.CAMPAIGNS_DIR = self._orig
        self._d.cleanup()

    def _rows(self):
        return xp._read_ledger("c")

    def test_an_award_is_recorded_with_what_it_landed_on(self):
        xp._record_award("c", [{"name": "Aldric", "awarded": 150,
                                "total_after": 1050}], note="medium combat")
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["character"], "Aldric")
        self.assertEqual(rows[0]["awarded"], 150)
        self.assertEqual(rows[0]["total_after"], 1050)
        self.assertIn("at", rows[0])

    def test_the_ledger_appends_rather_than_replaces(self):
        xp._record_award("c", [{"name": "A", "awarded": 10, "total_after": 10}], note="x")
        xp._record_award("c", [{"name": "A", "awarded": 10, "total_after": 20}], note="x")
        self.assertEqual(len(self._rows()), 2)

    def test_a_torn_line_does_not_lose_the_rest_of_the_ledger(self):
        xp._record_award("c", [{"name": "A", "awarded": 10, "total_after": 10}], note="x")
        p = xp._ledger_path("c")
        p.write_text(p.read_text(encoding="utf-8") + "{not json\n"
                     + json.dumps({"character": "B", "awarded": 5,
                                   "total_after": 5}) + "\n", encoding="utf-8")
        chars = {r["character"] for r in self._rows()}
        self.assertEqual(chars, {"A", "B"})

    def test_a_ledger_write_failure_never_costs_the_award(self):
        """The XP is already on the sheet by the time this runs."""
        xp.CAMPAIGNS_DIR = pathlib.Path("/proc/nonexistent-and-unwritable")
        try:
            xp._record_award("c", [{"name": "A", "awarded": 1,
                                    "total_after": 1}], note="x")
        except Exception as e:                     # noqa: BLE001
            self.fail(f"ledger write raised: {e}")

    def test_no_ledger_is_reported_rather_than_treated_as_clean(self):
        """An absent ledger and a reconciled one are different answers."""
        rows = xp._read_ledger("never-awarded")
        self.assertEqual(rows, [])


class CliWiringTests(unittest.TestCase):
    """The subcommand must be REGISTERED, not merely implemented.

    It was first added after `parser.parse_args()` had already run, so
    `xp.py check` died with "invalid choice" while cmd_check sat there
    complete. A function that exists and cannot be reached is the same as no
    function at all.
    """

    def test_check_is_reachable_from_the_command_line(self):
        import subprocess as sp
        r = sp.run([sys.executable, str(SCRIPTS / "xp.py"), "check",
                    "--campaign", "zz-not-a-campaign"],
                   capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertNotIn("invalid choice", (r.stderr or ""))
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
