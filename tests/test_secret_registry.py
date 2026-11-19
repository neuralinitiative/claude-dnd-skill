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
    - **Cover:** a human woman named Mara, a travelling scribe
    - **Known by:** Kael (S1, self) · Ilsa (S2, saw her shift)
    - **Suspected by:** Orrin Vale (S4, saw her face flicker, Insight 18)
    - **Must not learn it:** Captain Hale, the Order
    - **If revealed:** the Order hunts her

    ### envoy-letter — Éloise speaks for the party at the hearing
    - **Kind:** party
    - **Truth:** A letter sent ahead names Éloise the party's representative at the hearing in the capital.
    - **Cover:** *none*
    - **Known by:** Kael (S8, told), Éloise (S8, wrote it), the court clerk (S9, document)
    - **Suspected by:** *none*
    - **Must not learn it:** none

    ## Revealed / public

    ### guild-patron — The party works for the guild
    - **Known by:** everyone
""")


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.entries = sr.parse(REGISTRY)

    def test_placeholder_and_revealed_entries_are_skipped(self):
        self.assertEqual([e["id"] for e in self.entries], ["kael-changeling", "envoy-letter"])

    def test_fields_and_people(self):
        kael = self.entries[0]
        self.assertEqual(kael["label"], "Kael is a changeling")
        self.assertEqual(kael["cover"], "a human woman named Mara, a travelling scribe")
        self.assertEqual([n for n, _ in kael["known_people"]], ["Kael", "Ilsa"])
        # a comma inside parentheses stays with its person
        self.assertEqual(kael["suspected_people"],
                         [("Orrin Vale", "(S4, saw her face flicker, Insight 18)")])
        self.assertEqual([n for n, _ in kael["forbidden_people"]], ["Captain Hale", "the Order"])

    def test_none_values_are_empty(self):
        self.assertEqual(self.entries[1]["suspected_people"], [])
        self.assertEqual(self.entries[1]["forbidden_people"], [])


class NameMatchTests(unittest.TestCase):
    def test_short_name_matches_full_name(self):
        self.assertTrue(sr.names_match("Orrin", "Orrin Vale"))

    def test_accents_and_case_are_ignored(self):
        self.assertTrue(sr.names_match("eloise", "Éloise"))

    def test_shared_title_is_not_a_match(self):
        self.assertFalse(sr.names_match("Captain Brand", "Captain Hale"))


class BriefTests(unittest.TestCase):
    def setUp(self):
        self.entries = sr.parse(REGISTRY)

    def test_unaware_npc_gets_the_cover(self):
        out = sr.brief(self.entries, ["Captain Hale"])
        self.assertIn("kael-changeling", out)
        self.assertIn("COVER (what they perceive): a human woman named Mara", out)
        self.assertIn("Does NOT know: Captain Hale", out)
        self.assertIn("MUST NOT LEARN IT: Captain Hale — if revealed: the Order hunts her", out)

    def test_distant_arrangement_is_flagged_for_a_stranger(self):
        out = sr.brief(self.entries, ["Orrin"])
        self.assertIn("envoy-letter", out)
        self.assertRegex(out, r"envoy-letter[^\n]*\n(?:  [^\n]*\n)*  Does NOT know: Orrin")

    def test_suspicion_stays_a_hint(self):
        out = sr.brief(self.entries, ["Orrin"])
        self.assertIn("Only suspects (hints, never a flat statement): Orrin (S4,", out)

    def test_nothing_to_flag_when_everyone_present_knows(self):
        out = sr.brief(self.entries, ["Ilsa", "Kael"])
        self.assertNotIn("kael-changeling", out)

    def test_all_known_says_so(self):
        entries = [e for e in self.entries if e["id"] == "kael-changeling"]
        out = sr.brief(entries, ["Ilsa"])
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
            code, out = self._run("brief", "--file", str(p), "--present", "Captain Hale")
        self.assertEqual(code, 0)
        self.assertIn("Does NOT know: Captain Hale", out)

    def test_missing_registry_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = self._run("brief", "--file", str(pathlib.Path(d) / "secrets.md"), "--all")
        self.assertEqual(code, 0)
        self.assertIn("no knowledge registry", out)

    def test_script_does_not_shadow_stdlib_secrets(self):
        self.assertFalse((SKILL / "scripts" / "secrets.py").exists())


if __name__ == "__main__":
    unittest.main()
