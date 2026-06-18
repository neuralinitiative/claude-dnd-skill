"""
test_corpus_check.py — lazy-corpus layout validator (v2.2.0). Confirms the
arc.md / source-index.md / source/<id>.md consistency checks, and that a
non-corpus campaign (dynamic / sandbox / older import) is a clean no-op.

Run from repo root:
    python3 -m unittest tests.test_corpus_check -v
"""
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
SCRIPTS = SKILL / "scripts"


def _import(name, filename):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, str(SCRIPTS / filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CorpusCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cc = _import("corpus_check", "corpus_check.py")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["DND_CAMPAIGN_ROOT"] = self.tmp
        self.camp = pathlib.Path(self.tmp) / "campaigns" / "c"
        self.camp.mkdir(parents=True)

    def tearDown(self):
        os.environ.pop("DND_CAMPAIGN_ROOT", None)

    def _corpus(self, ids, indexed=None, with_arc=True):
        indexed = ids if indexed is None else indexed
        src = self.camp / "source"
        src.mkdir(exist_ok=True)
        for cid in ids:
            (src / f"{cid}.md").write_text("source text", encoding="utf-8")
        idx = "# Source index\n" + "\n".join(
            f"- {cid}: source/{cid}.md — scope" for cid in indexed
        )
        (self.camp / "source-index.md").write_text(idx, encoding="utf-8")
        if with_arc:
            (self.camp / "arc.md").write_text("# Arc\n", encoding="utf-8")

    def test_non_corpus_campaign_is_clean_noop(self):
        code, msgs = self.cc.check("c")
        self.assertEqual(code, 0)
        self.assertTrue(any("nothing to check" in m for m in msgs))

    def test_well_formed_corpus_passes(self):
        self._corpus(["1.1", "1.2", "2.1"])
        code, msgs = self.cc.check("c")
        self.assertEqual(code, 0, msg=msgs)

    def test_missing_source_file_flagged(self):
        self._corpus(["1.1", "1.2"], indexed=["1.1", "1.2", "1.3"])
        code, msgs = self.cc.check("c")
        self.assertEqual(code, 1)
        self.assertTrue(any("1.3" in m and "no source" in m for m in msgs))

    def test_orphan_source_file_flagged(self):
        self._corpus(["1.1", "1.2"], indexed=["1.1"])
        code, msgs = self.cc.check("c")
        self.assertEqual(code, 1)
        self.assertTrue(any("orphan" in m and "1.2" in m for m in msgs))

    def test_missing_arc_flagged(self):
        self._corpus(["1.1"], with_arc=False)
        code, msgs = self.cc.check("c")
        self.assertEqual(code, 1)
        self.assertTrue(any("arc.md" in m for m in msgs))

    def test_missing_campaign(self):
        code, msgs = self.cc.check("does-not-exist")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
