"""
test_utf8io.py — unit tests for the lossless text-read helper.

The helper's contract: never return text containing U+FFFD replacement chars.
Legacy-GBK files (written by the pre-sweep plugin on Chinese Windows) decode
losslessly — proven by a GBK round-trip — so a caller's write-back is a
one-time migration; anything else raises TextDecodeError.

Run from repo root:
    python3 -m unittest tests.test_utf8io -v
"""
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "scripts"))
from utf8io import read_text, TextDecodeError  # noqa: E402


class Utf8ioTests(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_utf8_passthrough(self):
        p = self.dir / "a.md"
        text = "精灵酒馆 #2 — 阿尔德里克\n"
        p.write_bytes(text.encode("utf-8"))
        self.assertEqual(read_text(p), text)

    def test_gbk_transcoded_losslessly(self):
        p = self.dir / "legacy.md"
        text = "18 塞尔潘 名字\n"
        p.write_bytes(text.encode("gbk"))
        got = read_text(p)
        self.assertEqual(got, text)
        self.assertNotIn("�", got)

    def test_gbk_round_trip_proof(self):
        p = self.dir / "mixed.md"
        raw = "中文「引号」§".encode("gbk")
        p.write_bytes(raw)
        self.assertEqual(read_text(p).encode("gbk"), raw)

    def test_neither_utf8_nor_gbk_raises(self):
        p = self.dir / "junk.bin"
        p.write_bytes(b"\xff\xfe\x00\x01\x80\x81\x9a")
        with self.assertRaises(TextDecodeError):
            read_text(p)

    def test_error_message_names_file(self):
        p = self.dir / "junk.bin"
        p.write_bytes(b"\xff\xfe")
        with self.assertRaises(TextDecodeError) as cm:
            read_text(p)
        self.assertIn(str(p), str(cm.exception))


if __name__ == "__main__":
    unittest.main()
