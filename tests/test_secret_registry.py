"""
test_secret_registry.py — the knowledge-registry scene brief.

The brief is what puts a cover story in front of the DM when an NPC is about to
speak, so the cases below are the leaks it exists to stop: a disguised PC, an
arrangement made by letter elsewhere, and a suspicion that must stay a hint.

Run from repo root:
    python3 -m unittest tests.test_secret_registry -v
"""
import contextlib
import io
import pathlib
import sys
import tempfile
import textwrap
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "scripts"))

import secret_registry as sr  # noqa: E402

REGISTRY = textwrap.dedent("""\
    # Knowledge Registry — test

    ## Secrets

    ### <entry-id> — <one-line label>
    - **Known by:** <Name>

    ### kael-changeling — Kael is a changeling
    - **Kind:** pc
    - **Truth:** Kael is a changeling who keeps a human form in public.
    - **Cover:** a human woman named Mara, a scribe from Waterdeep
    - **Known by:** Kael (S1, self) · Brisaile (S2, saw her shift)
    - **Suspected by:** Dace Morlenne (S4, saw her face flicker, Insight 18)
    - **Must not learn it:** Ser Garrick, the Order
    - **If revealed:** the Order hunts her

    ### valdrin-advocate — Valdrin speaks for Héonn at the trial
    - **Kind:** party
    - **Truth:** Valdrin's letter names him Héonn's representative at the Kolbrenn trial.
    - **Cover:** *none*
    - **Known by:** Héonn (S8, told), Valdrin (S8, wrote it), the court clerk (S9, document)
    - **Suspected by:** *none*
    - **Must not learn it:** none

    ## Revealed / public

    ### heonn-warforged — Héonn is a warforged
    - **Known by:** everyone
""")


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.entries = sr.parse(REGISTRY)

    def test_placeholder_and_revealed_entries_are_skipped(self):
        self.assertEqual([e["id"] for e in self.entries], ["kael-changeling", "valdrin-advocate"])

    def test_fields_and_people(self):
        kael = self.entries[0]
        self.assertEqual(kael["label"], "Kael is a changeling")
        self.assertEqual(kael["cover"], "a human woman named Mara, a scribe from Waterdeep")
        self.assertEqual([n for n, _ in kael["known_people"]], ["Kael", "Brisaile"])
        # a comma inside parentheses stays with its person
        self.assertEqual(kael["suspected_people"],
                         [("Dace Morlenne", "(S4, saw her face flicker, Insight 18)")])
        self.assertEqual([n for n, _ in kael["forbidden_people"]], ["Ser Garrick", "the Order"])

    def test_none_values_are_empty(self):
        self.assertEqual(self.entries[1]["suspected_people"], [])
        self.assertEqual(self.entries[1]["forbidden_people"], [])


class NameMatchTests(unittest.TestCase):
    def test_short_name_matches_full_name(self):
        self.assertTrue(sr.names_match("Dace", "Dace Morlenne"))

    def test_accents_and_case_are_ignored(self):
        self.assertTrue(sr.names_match("heonn", "Héonn"))

    def test_shared_title_is_not_a_match(self):
        self.assertFalse(sr.names_match("Ser Elouan", "Ser Garrick"))


class BriefTests(unittest.TestCase):
    def setUp(self):
        self.entries = sr.parse(REGISTRY)

    def test_unaware_npc_gets_the_cover(self):
        out = sr.brief(self.entries, ["Ser Garrick"])
        self.assertIn("kael-changeling", out)
        self.assertIn("COVER (what they perceive): a human woman named Mara", out)
        self.assertIn("Does NOT know: Ser Garrick", out)
        self.assertIn("MUST NOT LEARN IT: Ser Garrick — if revealed: the Order hunts her", out)

    def test_distant_arrangement_is_flagged_for_a_stranger(self):
        out = sr.brief(self.entries, ["Dace"])
        self.assertIn("valdrin-advocate", out)
        self.assertRegex(out, r"valdrin-advocate[^\n]*\n(?:  [^\n]*\n)*  Does NOT know: Dace")

    def test_suspicion_stays_a_hint(self):
        out = sr.brief(self.entries, ["Dace"])
        self.assertIn("Only suspects (hints, never a flat statement): Dace (S4,", out)

    def test_nothing_to_flag_when_everyone_present_knows(self):
        out = sr.brief(self.entries, ["Brisaile", "Kael"])
        self.assertNotIn("kael-changeling", out)

    def test_all_known_says_so(self):
        entries = [e for e in self.entries if e["id"] == "kael-changeling"]
        out = sr.brief(entries, ["Brisaile"])
        self.assertIn("No registered secret is hidden", out)


class CliTests(unittest.TestCase):
    def _run(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = sr.main(list(argv))
        return code, buf.getvalue()

    def test_brief_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "secrets.md"
            p.write_text(REGISTRY, encoding="utf-8")
            code, out = self._run("brief", "--file", str(p), "--present", "Ser Garrick")
        self.assertEqual(code, 0)
        self.assertIn("Does NOT know: Ser Garrick", out)

    def test_missing_registry_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = self._run("brief", "--file", str(pathlib.Path(d) / "secrets.md"), "--all")
        self.assertEqual(code, 0)
        self.assertIn("no knowledge registry", out)

    def test_script_does_not_shadow_stdlib_secrets(self):
        self.assertFalse((SKILL / "scripts" / "secrets.py").exists())


if __name__ == "__main__":
    unittest.main()
