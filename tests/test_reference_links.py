"""A reference link must point somewhere that exists, or not exist at all.

WHY
===
The "not in the local dataset" fallback built a URL by slugifying a name and
prefixing it by category. Nothing checked the page was there. When a name did
not slugify to the target site's convention the reader got a dead page, with no
signal before the click, and an unknown category degraded to a bare slug at the
site root — the least likely form to resolve.

Two rules come out of that:

  * a SUPPLEMENTAL record's stored URL always wins, because it was fetched and
    therefore resolves, and it holds non-SRD content the SRD reference does not;
  * a category with no verified mapping gets NO link. A guessed URL reads as an
    answer and dead-ends, which is worse than saying nothing.
"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "dnd" / "scripts"))
import lookup  # noqa: E402


class ReferenceUrlTests(unittest.TestCase):
    def test_a_supplemental_record_keeps_its_own_url(self):
        """Non-SRD content lives where it was fetched from. Never re-point it."""
        rec = {"wikidot_url": "https://dnd5e.wikidot.com/spell:toll-the-dead"}
        out = lookup.reference_url("Toll the Dead", category="spells", record=rec)
        self.assertEqual(out["url"], rec["wikidot_url"])

    def test_every_mapped_category_produces_a_url(self):
        for cat in ("spells", "conditions", "monsters", "equipment",
                    "magic_items", "features"):
            out = lookup.reference_url("Fireball", category=cat)
            self.assertTrue(out.get("url"), cat)
            self.assertTrue(out.get("label"), cat)

    def test_an_unmapped_category_produces_no_link_at_all(self):
        """The whole point: silence beats a guess."""
        self.assertEqual(lookup.reference_url("Thing", category="nonsense"), {})
        self.assertEqual(lookup.reference_url("Thing", category=None), {})

    def test_a_name_that_slugifies_to_nothing_produces_no_link(self):
        self.assertEqual(lookup.reference_url("!!!", category="spells"), {})

    def test_the_label_names_its_destination(self):
        """A link should say where it goes before it is clicked."""
        srd = lookup.reference_url("Fireball", category="spells")
        supp = lookup.reference_url(
            "X", category="spells", record={"wikidot_url": "https://example.test/x"}
        )
        self.assertNotEqual(srd["label"], supp["label"])

    def test_the_backcompat_shim_still_returns_a_bare_string(self):
        self.assertIsInstance(lookup.wikidot_url("Fireball", category="spells"), str)
        self.assertEqual(lookup.wikidot_url("Thing", category="nonsense"), "")


@unittest.skipUnless(
    os.environ.get("CDS_NETWORK_TESTS") == "1",
    "network test — set CDS_NETWORK_TESTS=1 to run",
)
class ReferenceUrlLiveTests(unittest.TestCase):
    """Opt-in. The only check that catches the destination reorganising."""

    #: One real SRD name per mapped category.
    CASES = [("Fireball", "spells"), ("Prone", "conditions"),
             ("Goblin", "monsters"), ("Longsword", "equipment"),
             ("Bag of Holding", "magic_items"), ("Rage", "features")]

    def _status(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": "otgm-tests/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_the_control_404s(self):
        """Without this, every 200 below could be a catch-all route."""
        url = lookup.reference_url("Definitely Not A Spell Xyzzy",
                                   category="spells")["url"]
        self.assertEqual(self._status(url), 404,
                         "a fake slug resolved — 200s prove nothing")

    def test_every_mapped_category_resolves(self):
        for name, cat in self.CASES:
            url = lookup.reference_url(name, category=cat)["url"]
            self.assertEqual(self._status(url), 200, f"{cat}: {url}")


if __name__ == "__main__":
    unittest.main()
