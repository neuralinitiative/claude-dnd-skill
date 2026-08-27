"""
test_deterministic_extract.py — unit tests for the pattern-based extractor.

Run from repo root:
    python3 -m unittest tests.test_deterministic_extract -v
"""
import json
import pathlib
import sys
import tempfile
import textwrap
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "scripts"))

import graph_extract_deterministic as det  # noqa: E402


class EntityRecognizerTests(unittest.TestCase):

    def test_npcs_md_table_extraction(self):
        with tempfile.TemporaryDirectory() as td:
            campaign = pathlib.Path(td)
            (campaign / "npcs.md").write_text(textwrap.dedent("""\
                # NPCs

                | Name | Faction | Status |
                |------|---------|--------|
                | Aldric Brandt | The Council | alive |
                | Mira | Thornblood | ally |
                | Captain Voss | None | hostile |
            """), encoding="utf-8")
            ents = det.build_entity_set(campaign)
            self.assertIn("Aldric Brandt", ents)
            self.assertIn("Mira", ents)
            self.assertIn("Captain Voss", ents)
            self.assertNotIn("Name", ents)
            self.assertNotIn("------", ents)

    def test_npcs_full_md_headings(self):
        with tempfile.TemporaryDirectory() as td:
            campaign = pathlib.Path(td)
            (campaign / "npcs-full.md").write_text(textwrap.dedent("""\
                # Full NPC Entries

                ### Aldric Brandt
                Old man. Knows the woods.

                ### Captain Voss
                Hard-eyed. Distrusts strangers.
            """), encoding="utf-8")
            ents = det.build_entity_set(campaign)
            self.assertEqual({"Aldric Brandt", "Captain Voss"}, ents)

    def test_world_md_factions_and_places(self):
        with tempfile.TemporaryDirectory() as td:
            campaign = pathlib.Path(td)
            (campaign / "world.md").write_text(textwrap.dedent("""\
                # World

                ## Factions

                ### The Iron Guild
                Mercantile cartel.

                ## Locations

                ### Greyholm Harbour
                Deep-water port.
            """), encoding="utf-8")
            ents = det.build_entity_set(campaign)
            # We preserve the leading article — both forms count
            self.assertTrue("Iron Guild" in ents or "The Iron Guild" in ents,
                            f"expected Iron Guild (or The Iron Guild) in {ents}")
            self.assertIn("Greyholm Harbour", ents)

    def test_likely_name_filter(self):
        self.assertTrue(det._is_likely_name("Aldric Brandt"))
        self.assertTrue(det._is_likely_name("The Council"))
        self.assertFalse(det._is_likely_name("name"))
        self.assertFalse(det._is_likely_name("---"))
        self.assertFalse(det._is_likely_name("|"))
        self.assertFalse(det._is_likely_name(""))
        self.assertFalse(det._is_likely_name(""))


class AliasIndexTests(unittest.TestCase):

    def test_canonical_self_maps(self):
        ents = {"Aldric Brandt", "Mira"}
        a = det.build_alias_index(ents)
        self.assertEqual(a["Aldric Brandt"], "Aldric Brandt")
        self.assertEqual(a["Mira"], "Mira")

    def test_first_word_alias_for_multiword(self):
        ents = {"Aldric Brandt"}
        a = det.build_alias_index(ents)
        self.assertEqual(a["Aldric"], "Aldric Brandt")

    def test_surname_alias_for_multiword(self):
        ents = {"Aldric Brandt"}
        a = det.build_alias_index(ents)
        self.assertEqual(a["Brandt"], "Aldric Brandt")

    def test_middle_subsequence_alias(self):
        """For 'Mayor Aldric Brandt', 'Aldric Brandt' (sans title) is a useful alias."""
        ents = {"Mayor Aldric Brandt"}
        a = det.build_alias_index(ents)
        self.assertEqual(a.get("Aldric Brandt"), "Mayor Aldric Brandt")
        self.assertEqual(a.get("Mayor Aldric"), "Mayor Aldric Brandt")

    def test_stop_words_never_become_aliases(self):
        """'The Council' must NOT yield 'The' as an alias."""
        ents = {"The Council", "Aldric Brandt"}
        a = det.build_alias_index(ents)
        self.assertNotIn("The", a)
        self.assertNotIn("the", a)
        # Council itself (post-stop-word) is fine
        self.assertEqual(a.get("Council"), "The Council")

    def test_ambiguous_first_word_skipped(self):
        """If two NPCs share a first word, neither is a valid alias for it alone."""
        ents = {"Aldric Brandt", "Aldric Cors"}
        a = det.build_alias_index(ents)
        self.assertNotIn("Aldric", a)
        # Surnames are still unambiguous
        self.assertEqual(a.get("Brandt"), "Aldric Brandt")
        self.assertEqual(a.get("Cors"), "Aldric Cors")

    def test_canonical_takes_precedence_over_alias(self):
        """If a single-word canonical exists, it must shadow any same-spelled alias."""
        ents = {"Mira", "Mira Solveig"}
        a = det.build_alias_index(ents)
        # 'Mira' should map to itself, not to Mira Solveig
        self.assertEqual(a["Mira"], "Mira")


class SentenceSplitTests(unittest.TestCase):

    def test_basic_sentence_split(self):
        text = "Aldric met Mira at the docks. They spoke briefly. Voss watched from the rigging."
        sents = det.split_sentences(text)
        self.assertEqual(len(sents), 3)
        self.assertIn("Aldric met Mira at the docks.", sents)

    def test_short_stubs_filtered(self):
        text = "OK. Yes. He spoke at length about the council and its many failings."
        sents = det.split_sentences(text)
        # "OK." and "Yes." should be filtered as too short
        self.assertEqual(len(sents), 1)


class PatternRegexTests(unittest.TestCase):

    def test_simple_svo_pattern(self):
        ent_alt = det._build_entity_alternation({"Aldric", "Mira"})
        pat = det.build_pattern_regex("X met Y", ent_alt)
        self.assertIsNotNone(pat)
        m = pat.search("Aldric met Mira at the docks.")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("X"), "Aldric")
        self.assertEqual(m.group("Y"), "Mira")

    def test_svo_with_prep_pattern(self):
        ent_alt = det._build_entity_alternation({"Aldric", "Mira", "Voss"})
        pat = det.build_pattern_regex("X sent Y to Z", ent_alt)
        self.assertIsNotNone(pat)
        m = pat.search("Aldric sent Mira to Voss with a message.")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("X"), "Aldric")
        self.assertEqual(m.group("Y"), "Mira")
        self.assertEqual(m.group("Z"), "Voss")

    def test_multiword_entity_resolved(self):
        ent_alt = det._build_entity_alternation({"Captain Voss", "Voss", "Aldric"})
        pat = det.build_pattern_regex("X met Y", ent_alt)
        m = pat.search("Aldric met Captain Voss in the chart room.")
        # Longest-first ordering means we should match "Captain Voss" not just "Voss"
        self.assertEqual(m.group("Y"), "Captain Voss")

    def test_no_entity_alternation_returns_none(self):
        pat = det.build_pattern_regex("X met Y", "")
        self.assertIsNone(pat)


class SessionForOffsetTests(unittest.TestCase):

    def test_finds_most_recent_session_header(self):
        text = "## Session 1\n\nIntro stuff.\n\n## Session 2\n\nMore stuff happened here."
        offset = text.find("More stuff")
        self.assertEqual(det.session_for_offset(text, offset), 2)

    def test_no_session_header_returns_none(self):
        text = "Just some prose with no session header at all."
        self.assertIsNone(det.session_for_offset(text, 5))


class FutureTenseVerbTests(unittest.TestCase):
    """v0.6 additions — future-tense planning verbs in DM session-prep prose."""

    def setUp(self):
        self.ents = {"Vedra", "Aldric Brandt", "Mira", "Voss"}
        self.alt = det._build_entity_alternation(
            set(det.build_alias_index(self.ents).keys())
        )

    def _match(self, template, sent):
        pat = det.build_pattern_regex(template, self.alt)
        return pat.search(sent) if pat else None

    def test_v_wildcard_matches_variable_verb_phrase(self):
        m = self._match("X plans to V Y", "Vedra plans to meet Aldric Brandt at dawn.")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("X"), "Vedra")
        self.assertEqual(m.group("Y"), "Aldric Brandt")

    def test_v_wildcard_does_not_consume_capitalized_entity(self):
        """V must be lowercase-only so it can't eat 'Aldric' before reaching 'Brandt'."""
        m = self._match("X plans to V Y", "Mira plans to track Aldric Brandt all night.")
        self.assertIsNotNone(m)
        # Y should be the FULL canonical, not just the surname
        self.assertEqual(m.group("Y"), "Aldric Brandt")

    def test_intends_to_pattern(self):
        m = self._match("X intends to V Y", "Mira intends to confront Voss before dusk.")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("X"), "Mira")
        self.assertEqual(m.group("Y"), "Voss")

    def test_scheduled_to_pattern(self):
        m = self._match("X is scheduled to V Y",
                        "Vedra is scheduled to meet Aldric Brandt next session.")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("X"), "Vedra")

    def test_targets_pattern(self):
        m = self._match("X targets Y", "Vedra targets Mira at the harbor.")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("X"), "Vedra")
        self.assertEqual(m.group("Y"), "Mira")

    def test_aims_to_pattern(self):
        m = self._match("X aims to V Y", "Voss aims to capture Mira before sundown.")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("X"), "Voss")
        self.assertEqual(m.group("Y"), "Mira")

    def test_extract_finds_future_tense_relationships_end_to_end(self):
        """Full extractor pipeline picks up future-tense edges from a session-log."""
        with tempfile.TemporaryDirectory() as td:
            campaign = pathlib.Path(td)
            (campaign / "npcs.md").write_text(textwrap.dedent("""\
                | Vedra | x |
                | Aldric Brandt | y |
                | Mira | z |
            """), encoding="utf-8")
            (campaign / "session-log.md").write_text(textwrap.dedent("""\
                # Session Log

                ## Session 1

                Vedra plans to meet Aldric Brandt at dawn. Mira intends to confront Vedra. Vedra targets Mira.
            """), encoding="utf-8")
            proposals = det.extract_proposals(campaign)
            edges = {(p["from"], p["to"], p["type"]) for p in proposals}
            self.assertIn(("Vedra", "Aldric Brandt", "plans_to"), edges,
                          f"missing plans_to edge; got {edges}")
            self.assertIn(("Mira", "Vedra", "intends_to"), edges,
                          f"missing intends_to edge; got {edges}")
            self.assertIn(("Vedra", "Mira", "targets"), edges,
                          f"missing targets edge; got {edges}")


class ExtractEndToEndTests(unittest.TestCase):

    def _build_campaign(self, td: str):
        """Build a tiny synthetic campaign with known relationships."""
        campaign = pathlib.Path(td)

        (campaign / "npcs.md").write_text(textwrap.dedent("""\
            # NPCs

            | Name | Notes |
            |------|-------|
            | Aldric Brandt | wood elf, retired |
            | Mira | thornblood ally |
            | Captain Voss | sea captain |
        """), encoding="utf-8")

        (campaign / "session-log.md").write_text(textwrap.dedent("""\
            # Session Log

            ## Session 1

            Aldric met Mira at the docks. They spoke for a long time. Aldric sent Mira to Captain Voss with a sealed letter. Captain Voss attacked Aldric later that night. Mira fears Captain Voss now.

            ## Session 2

            Captain Voss killed Aldric in the alley behind the inn. Mira swore an oath to find him.
        """), encoding="utf-8")
        return campaign

    def test_extract_finds_known_relationships(self):
        with tempfile.TemporaryDirectory() as td:
            campaign = self._build_campaign(td)
            proposals = det.extract_proposals(campaign)
            self.assertGreater(len(proposals), 0, "expected at least some proposals")

            # Check shape of first proposal
            for p in proposals:
                self.assertIn("from", p)
                self.assertIn("to", p)
                self.assertIn("type", p)
                self.assertIn("source", p)
                self.assertIn("anchor", p["source"])
                self.assertIn("session", p["source"])
                self.assertIn("file", p["source"])

    def test_extract_finds_specific_edges(self):
        with tempfile.TemporaryDirectory() as td:
            campaign = self._build_campaign(td)
            proposals = det.extract_proposals(campaign)

            # Edges by (from, to, type) tuple
            edges = {(p["from"], p["to"], p["type"]) for p in proposals}

            # "Aldric met Mira" should produce a met edge
            self.assertIn(("Aldric Brandt", "Mira", "met"), edges,
                          f"expected Aldric met Mira; got {edges}")

            # "Captain Voss attacked Aldric" should produce an attacked edge
            self.assertIn(("Captain Voss", "Aldric Brandt", "attacked"), edges,
                          f"expected Voss attacked Aldric; got {edges}")

            # "Captain Voss killed Aldric" should produce a killed edge
            self.assertIn(("Captain Voss", "Aldric Brandt", "killed"), edges,
                          f"expected Voss killed Aldric; got {edges}")

    def test_extract_session_numbers_correct(self):
        with tempfile.TemporaryDirectory() as td:
            campaign = self._build_campaign(td)
            proposals = det.extract_proposals(campaign)
            for p in proposals:
                # Session 1 edges should have since_session == 1; session 2 → 2
                anchor_in = p["source"]["anchor"]
                if "killed Aldric" in anchor_in or "swore an oath" in anchor_in:
                    self.assertEqual(p["source"]["session"], 2,
                                     f"expected session 2 for: {anchor_in}")
                elif "met Mira" in anchor_in or "attacked Aldric" in anchor_in:
                    self.assertEqual(p["source"]["session"], 1,
                                     f"expected session 1 for: {anchor_in}")

    def test_extract_dedupes_identical_proposals(self):
        """Same edge appearing in multiple sentences should be deduped on (from,to,type,anchor)."""
        with tempfile.TemporaryDirectory() as td:
            campaign = self._build_campaign(td)
            proposals = det.extract_proposals(campaign)
            keys = [(p["from"], p["to"], p["type"], p["source"]["anchor"]) for p in proposals]
            self.assertEqual(len(keys), len(set(keys)), "duplicate proposals leaked through")

    def test_empty_campaign_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as td:
            campaign = pathlib.Path(td)
            (campaign / "npcs.md").write_text("# NPCs\n\nNone yet.\n", encoding="utf-8")
            proposals = det.extract_proposals(campaign)
            self.assertEqual(proposals, [])

    def test_last_session_only_skips_archive(self):
        with tempfile.TemporaryDirectory() as td:
            campaign = pathlib.Path(td)
            (campaign / "npcs.md").write_text("| Aldric | x |\n| Mira | y |\n", encoding="utf-8")
            (campaign / "session-log-archive.md").write_text(
                "## Session 1\nAldric met Mira at dawn.\n"
            , encoding="utf-8")
            (campaign / "session-log.md").write_text(
                "## Session 2\nAldric attacked Mira at dusk.\n"
            , encoding="utf-8")
            proposals = det.extract_proposals(campaign, last_session_only=True)
            anchors = [p["source"]["anchor"] for p in proposals]
            self.assertTrue(any("attacked" in a for a in anchors),
                            f"expected attacked edge from session-log.md; got {anchors}")
            self.assertFalse(any("dawn" in a for a in anchors),
                             f"archive should be skipped with --last-session-only")


if __name__ == "__main__":
    unittest.main()
