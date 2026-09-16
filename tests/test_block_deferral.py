"""A block arriving mid-narration must wait, not dump the paragraph.

WHY
===
`_flushForBlock()` called `instantFlush()`: an NPC line, a dice result or a
tutor note arriving while the narrator was still typing snapped the rest of the
prose to its end so the block could land cleanly underneath. It never broke a
word — it just threw the reveal away, and the block that arrived is usually the
consequence of the sentence being read.

The replacement holds the block until the typewriter genuinely drains. Two
things are easy to get wrong and are what these tests actually check:

  * the idle check must RE-ARM. Narration frequently continues after a block
    arrives, so a single "wait N ms then flush" lands in the middle of the next
    paragraph.
  * a stalled reveal must not swallow the queue. If the typewriter never
    drains, a dice result the table is waiting on has to appear anyway.

Driven through node rather than asserted against the source, because "the
function exists" is exactly what was true of the badge code that never ran.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = ROOT / "skills" / "dnd" / "display" / "templates" / "index.html"
NODE = shutil.which("node")

#: Pull just the deferral machinery out of the template. Keeping the slice
#: narrow means this test does not need the DOM.
_SLICE = re.compile(
    r"(const PENDING_BLOCK_GAP.*?)\n// ── Shared: after inserting", re.S)


def _harness(script: str) -> dict:
    src = HTML.read_text(encoding="utf-8")
    m = _SLICE.search(src)
    if not m:
        raise AssertionError("deferral block not found in the template")
    prelude = """
let isTyping = false;
let charQueue = [];
//: The valve path calls instantFlush() — stubbed so the harness exercises the
//: real branch rather than crashing on a DOM function it has no use for.
let instantFlushCalls = 0;
function instantFlush() { instantFlushCalls++; charQueue.length = 0; isTyping = false; }
"""
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                     encoding="utf-8") as f:
        f.write(prelude + m.group(1) + "\n" + script)
        path = f.name
    out = subprocess.run([NODE, path], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    pathlib.Path(path).unlink()
    if out.returncode != 0:
        raise AssertionError(out.stderr[:2000])
    return json.loads(out.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "node not available")
class BlockDeferralTests(unittest.TestCase):

    def test_an_idle_typewriter_renders_immediately(self):
        r = _harness("""
        let landed = [];
        _deferBlock(() => landed.push('dice'), false);
        console.log(JSON.stringify({ immediate: landed.length === 1 }));
        """)
        self.assertTrue(r["immediate"])

    def test_a_block_arriving_mid_reveal_is_held(self):
        r = _harness("""
        isTyping = true;
        let landed = [];
        _deferBlock(() => landed.push('dice'), false);
        console.log(JSON.stringify({ heldAtOnce: landed.length === 0 }));
        """)
        self.assertTrue(r["heldAtOnce"], "the block rendered instead of waiting")

    def test_it_releases_once_the_typewriter_drains(self):
        r = _harness("""
        isTyping = true;
        let landed = [];
        _deferBlock(() => landed.push('dice'), false);
        setTimeout(() => { isTyping = false; }, 400);
        setTimeout(() => {
          console.log(JSON.stringify({ released: landed.length === 1 }));
          process.exit(0);
        }, 1600);
        """)
        self.assertTrue(r["released"], "the block never appeared after draining")

    def test_the_check_re_arms_across_continuing_narration(self):
        """The bug a single timeout produces: release mid-next-paragraph."""
        r = _harness("""
        isTyping = true;
        let landed = [];
        let stillTypingWhenLanded = null;
        _deferBlock(() => {
          landed.push('dice');
          stillTypingWhenLanded = isTyping;
        }, false);
        // narration keeps going well past one PENDING_BLOCK_GAP
        setTimeout(() => { isTyping = false; }, 1500);
        setTimeout(() => {
          console.log(JSON.stringify({
            landed: landed.length,
            landedWhileTyping: stillTypingWhenLanded === true,
            flushes: instantFlushCalls,
          }));
          process.exit(0);
        }, 2600);
        """)
        self.assertEqual(r["landed"], 1)
        # instantFlushCalls is the load-bearing assertion. A fire-once timer
        # still DELIVERS the block — it just dumps the prose to do it, and the
        # flush resets isTyping, so checking isTyping at landing time cannot
        # tell the two apart. Counting flushes can.
        self.assertEqual(
            r["flushes"], 0,
            "the reveal was dumped to deliver the block — the check fired "
            "once instead of re-arming",
        )
        self.assertFalse(r["landedWhileTyping"])

    def test_a_stalled_reveal_still_releases(self):
        """The valve. A queue that never drains must not swallow a dice roll."""
        r = _harness("""
        isTyping = true;            // never drains
        let landed = [];
        _deferBlock(() => landed.push('dice'), false);
        setTimeout(() => {
          console.log(JSON.stringify({ landed: landed.length }));
          process.exit(0);
        }, PENDING_BLOCK_MAX + 1200);
        """)
        self.assertEqual(r["landed"], 1, "a stalled reveal swallowed the block")

    def test_order_is_preserved_across_held_blocks(self):
        r = _harness("""
        isTyping = true;
        let landed = [];
        _deferBlock(() => landed.push('a'), false);
        _deferBlock(() => landed.push('b'), false);
        _deferBlock(() => landed.push('c'), false);
        setTimeout(() => { isTyping = false; }, 300);
        setTimeout(() => {
          console.log(JSON.stringify({ order: landed.join('') }));
          process.exit(0);
        }, 1600);
        """)
        self.assertEqual(r["order"], "abc")

    def test_a_replay_is_never_deferred(self):
        """History dumps have no live reveal to protect."""
        r = _harness("""
        isTyping = true;
        let landed = [];
        _deferBlock(() => landed.push('x'), true);
        console.log(JSON.stringify({ immediate: landed.length === 1 }));
        """)
        self.assertTrue(r["immediate"])


if __name__ == "__main__":
    unittest.main()
