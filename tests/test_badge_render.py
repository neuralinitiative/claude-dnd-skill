"""Badges must RENDER, not merely be implemented.

WHY THIS TEST EXISTS
====================
The badge feature shipped dead: a keyword table, a function that built the
image, and a CSS class, with nothing calling the function. The first fix wired
it to one renderer and the static tests went green — while dice and tutor
blocks still got nothing, because they are the two blocks that never receive a
TTS bar and that was the hook the fix had picked.

Both rounds of static tests passed against a feature no player could see. The
only check that separates "implemented" from "visible" is rendering the page
and looking at the DOM, so that is what this does.

Opt-in because it needs a browser and a running display:

    DISPLAY_RENDER_TESTS=1 python3 -m pytest tests/test_badge_render.py
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PORT = os.environ.get("DISPLAY_PORT", "5001")
ENABLED = os.environ.get("DISPLAY_RENDER_TESTS") == "1"

try:
    from playwright.sync_api import sync_playwright
    HAVE_PW = True
except ImportError:
    HAVE_PW = False

#: prose -> the badge it must produce. Two are KIND-driven (npc, dice, tutor),
#: which is the path that must work in any language; the rest are semantic.
CASES = [
    (["The road bends east toward the village, and a gate stands open."], "location.png"),
    (["Well met, traveller.", "--npc", "Vesna"], "chat.png"),
    (["d20+4 = 18 vs DC 15 — success", "--dice"], "attack.png"),
    (["A chest sits in the corner, and inside is a pouch of gold coin."], "treasure.png"),
    (["Remember: a saving throw uses your own modifier.", "--tutor"], "scroll.png"),
    (["A rune flares and the air crackles with arcane light."], "crystal_ball.png"),
]


@unittest.skipUnless(ENABLED and HAVE_PW,
                     "set DISPLAY_RENDER_TESTS=1 with a display running on "
                     f"port {PORT} (and playwright installed)")
class BadgeRenderTests(unittest.TestCase):

    SEND = None          # set by subclass / discovery below

    @classmethod
    def setUpClass(cls):
        cls.send_py = cls.SEND or _discover_send()
        subprocess.run(["curl", "-s", "-X", "POST",
                        f"http://127.0.0.1:{PORT}/clear",
                        "-H", "Content-Type: application/json", "-d", "{}"],
                       capture_output=True)
        time.sleep(1)
        for args, _ in CASES:
            text, flags = args[0], args[1:]
            subprocess.run([sys.executable, str(cls.send_py), *flags],
                           input=text, capture_output=True, text=True,
                           encoding="utf-8", cwd=str(ROOT))
            time.sleep(1.2)

    def test_every_block_kind_renders_its_badge(self):
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="domcontentloaded")
            pg.wait_for_timeout(11000)
            rows = pg.evaluate("""() =>
              [...document.querySelectorAll('#text-content > div')].map(el => ({
                cls: (el.className||'').split(' ')[0],
                badge: (el.querySelector('.block-badge')?.getAttribute('src')||'')
                         .split('/').pop() || null,
                width: Math.round(el.querySelector('.block-badge')
                         ?.getBoundingClientRect().width || 0),
              })).filter(r => r.cls && r.cls !== 'divider')""")
            b.close()

        self.assertTrue(rows, "no blocks rendered at all — the scan is blind")
        self.assertEqual(errors, [], f"page errors: {errors}")

        got = [r["badge"] for r in rows]
        want = [icon for _, icon in CASES]
        self.assertEqual(
            got, want,
            "badges did not render as expected.\n"
            f"  got : {got}\n  want: {want}\n"
            "  A None here is the feature being dead for that block kind.",
        )
        for r in rows:
            self.assertGreater(
                r["width"], 0,
                f"{r['cls']} badge has zero width — created but not styled",
            )


def _discover_send() -> pathlib.Path:
    for rel in ("display/send.py", "skills/dnd/display/send.py"):
        p = ROOT / rel
        if p.exists():
            return p
    raise AssertionError("send.py not found")


if __name__ == "__main__":
    unittest.main()
