"""
test_import_columns.py — column-aware reading-order logic for PDF import (v2.2.1).
Verifies that order_blocks() de-columns two-column pages, keeps single-column
pages intact, and treats full-width headers as band dividers. Pure geometry — no
PyMuPDF or PDF needed.

Run from repo root:
    python3 -m unittest tests.test_import_columns -v
"""
import importlib.util
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
SCRIPTS = SKILL / "scripts"


def _import():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "import_campaign", str(SCRIPTS / "import_campaign.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Page is 600 wide; left column ~[40,290], right column ~[310,560].
W = 600
def L(y, text, h=20): return (40, y, 290, y + h, text)
def R(y, text, h=20): return (310, y, 560, y + h, text)
def FULL(y, text, h=20): return (40, y, 560, y + h, text)


class OrderBlocksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ic = _import()

    def order(self, blocks):
        return self.ic.order_blocks(blocks, W)

    def test_two_columns_read_left_then_right(self):
        # Deliberately pass them interleaved/scrambled, like a raw extraction would.
        blocks = [R(100, "r1"), L(200, "l2"), L(100, "l1"), R(200, "r2")]
        self.assertEqual(self.order(blocks), ["l1", "l2", "r1", "r2"])

    def test_keyed_rooms_come_out_in_sequence(self):
        # The exact failure mode: -layout yields 1,2,4,3; column-aware must give 1..4.
        blocks = [L(100, "1"), R(100, "3"), L(140, "2"), R(140, "4")]
        self.assertEqual(self.order(blocks), ["1", "2", "3", "4"])

    def test_full_width_title_comes_first(self):
        blocks = [L(100, "l1"), R(100, "r1"), FULL(10, "TITLE")]
        self.assertEqual(self.order(blocks), ["TITLE", "l1", "r1"])

    def test_full_width_header_divides_bands(self):
        # header splits the page: section A (above) then header then section B (below)
        blocks = [
            L(100, "a-l"), R(100, "a-r"),
            FULL(200, "SECTION B"),
            L(300, "b-l"), R(300, "b-r"),
        ]
        self.assertEqual(
            self.order(blocks),
            ["a-l", "a-r", "SECTION B", "b-l", "b-r"],
        )

    def test_single_column_sorted_top_to_bottom(self):
        # Full-width prose blocks => single column => pure vertical order.
        blocks = [FULL(300, "third"), FULL(100, "first"), FULL(200, "second")]
        self.assertEqual(self.order(blocks), ["first", "second", "third"])

    def test_empty_blocks_dropped(self):
        blocks = [L(100, "l1"), L(120, "   "), R(100, "r1")]
        self.assertEqual(self.order(blocks), ["l1", "r1"])

    def test_no_blocks(self):
        self.assertEqual(self.order([]), [])

    def test_zero_width_page_falls_back_to_vertical(self):
        blocks = [(0, 200, 0, 220, "b"), (0, 100, 0, 120, "a")]
        self.assertEqual(self.ic.order_blocks(blocks, 0), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
