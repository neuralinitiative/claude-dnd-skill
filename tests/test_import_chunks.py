"""Import chunks are sized by text, not by spaces, and keep the source's lines.

WHY
===
`import_campaign.py` chunked with `text.split()` at 4,000 "words" and rejoined
with `" ".join`. Two failures from the same two calls:

  * **CJK was one chunk.** Chinese and Japanese put no spaces between words,
    so a 48,000-character Chinese module counted as 1 word and came back as 1
    chunk -- a single 144 KB read, far past what the chunking exists to keep
    the model under.
  * **Every module lost its line structure.** Rejoining on a space flattened
    every newline, so markdown headings, tables and stat blocks arrived as one
    long line in every language, English included -- the structure the import
    step segments chapters by.

Chunks are now cut on line boundaries by weight (a wide CJK character weighs
4, anything else 1), so English and CJK chunks are comparable reads and
`"".join(chunks)` reproduces the source exactly. `--chunk N` is unchanged.
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location("import_campaign_chunks", SCRIPTS / "import_campaign.py")
ic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ic)

ZH = ("# 第一章 黑暗森林\n\n| 名字 | 等級 |\n|---|---|\n| 哥布林 | 1 |\n\n"
      + "冒險者們走進了森林，四周一片寂靜。\n" * 2800)
EN = ("## Chapter 1: The Dark Wood\n\n| Name | CR |\n|---|---|\n| Goblin | 1/4 |\n\n"
      + "The adventurers walk into the dark forest and listen for the wolves.\n" * 2200)


class ChunkTests(unittest.TestCase):
    def test_cjk_module_is_more_than_one_chunk(self):
        self.assertGreater(len(ZH), 48000)
        self.assertGreaterEqual(ic.total_chunks(ZH), 8)

    def test_chunks_reassemble_to_the_source_exactly(self):
        for text in (ZH, EN):
            self.assertEqual("".join(ic.split_chunks(text)), text)

    def test_line_structure_survives(self):
        first = ic.chunk_text(ZH, 0)
        self.assertTrue(first.startswith("# 第一章 黑暗森林\n\n| 名字 | 等級 |\n|---|---|\n"))
        self.assertIn("\n| Goblin | 1/4 |\n", ic.chunk_text(EN, 0))

    def test_chunks_break_on_lines(self):
        for c in ic.split_chunks(EN)[:-1]:
            self.assertTrue(c.endswith("\n"))

    def test_latin_and_cjk_chunks_are_comparable_reads(self):
        # About 4,000 English words or about 6,000 CJK characters per chunk.
        en = ic.split_chunks(EN)[0]
        zh = ic.split_chunks(ZH)[0]
        self.assertTrue(3500 <= len(en.split()) <= 4500, len(en.split()))
        self.assertTrue(5000 <= len(zh) <= 7000, len(zh))

    def test_a_line_longer_than_a_chunk_is_cut_not_returned_whole(self):
        wall = "字" * 20000
        chunks = ic.split_chunks(wall)
        self.assertEqual("".join(chunks), wall)
        self.assertTrue(all(len(c) <= ic.CHUNK_WEIGHT // ic.WIDE_WEIGHT for c in chunks))

    def test_short_source_is_one_chunk(self):
        self.assertEqual(ic.total_chunks("# Title\n\nA short module.\n"), 1)


class CliTests(unittest.TestCase):
    def test_chunk_interface_and_out_of_range(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "module.md"
            p.write_text(ZH, encoding="utf-8")
            run = lambda *a: subprocess.run([sys.executable, str(SCRIPTS / "import_campaign.py"),
                                             str(p), *a], capture_output=True)
            n = int(run("--chunks").stdout)
            self.assertGreaterEqual(n, 8)
            out = run("--chunk", "0").stdout.decode("utf-8")
            self.assertIn("| 名字 | 等級 |\n", out.replace("\r\n", "\n"))
            self.assertEqual(run("--chunk", str(n)).returncode, 1)


if __name__ == "__main__":
    unittest.main()
