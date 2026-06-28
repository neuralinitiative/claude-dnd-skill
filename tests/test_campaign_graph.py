"""
test_campaign_graph.py — end-to-end smoke tests for the campaign_graph CLI.

Builds an ephemeral campaign in /tmp, runs the actual `campaign_graph.py`
subcommands as subprocesses (against `GM_CAMPAIGN_ROOT` so paths.py routes
correctly), and verifies the on-disk graph.json matches expectations.

Run from repo root:
    python3 -m unittest tests.test_campaign_graph -v
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
# Code lives under skills/dnd/ in the plugin layout; fall back to the repo root
# for a legacy standalone checkout.
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
SCRIPT = SKILL / "scripts" / "campaign_graph.py"


def _run(args, env_overrides=None, stdin_text=""):
    """Run campaign_graph.py with args, return (returncode, stdout, stderr)."""
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env, input=stdin_text,
    )
    return r.returncode, r.stdout, r.stderr


class GraphSubcommandTests(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.td.name)
        # paths.py expects campaigns at $DND_CAMPAIGN_ROOT/campaigns/<name>/.
        # Unique campaign name guarantees no collision with the user's real
        # ~/.claude/dnd/campaigns/ legacy fallback path.
        self.env = {"DND_CAMPAIGN_ROOT": str(self.root)}
        self.campaign = f"unittest-graph-{os.getpid()}"
        self.camp_dir = self.root / "campaigns" / self.campaign
        self.camp_dir.mkdir(parents=True)
        (self.camp_dir / "npcs.md").write_text("| Aldric | x |\n| Mira | y |\n")

    def tearDown(self):
        self.td.cleanup()

    def _graph_json(self) -> dict:
        gp = self.camp_dir / "graph.json"
        if not gp.exists():
            return {"nodes": [], "edges": []}
        return json.loads(gp.read_text())

    # ── add-node + add-edge + list ─────────────────────────────────────────

    def test_add_node_creates_graph_json(self):
        rc, out, err = _run(
            ["add-node", "--campaign", self.campaign,
             "--type", "npc", "--name", "Aldric"], env_overrides=self.env
        )
        self.assertEqual(rc, 0, msg=err)
        data = self._graph_json()
        self.assertEqual(len(data["nodes"]), 1)
        self.assertEqual(data["nodes"][0]["name"], "Aldric")
        self.assertEqual(data["nodes"][0]["type"], "npc")

    def test_add_edge_after_two_nodes(self):
        for name in ("Aldric", "Mira"):
            _run(["add-node", "--campaign", self.campaign,
                  "--type", "npc", "--name", name], env_overrides=self.env)
        rc, out, err = _run(
            ["add-edge", "--campaign", self.campaign,
             "--from", "Aldric", "--to", "Mira",
             "--type", "knows", "--since", "1"], env_overrides=self.env
        )
        self.assertEqual(rc, 0, msg=err)
        data = self._graph_json()
        self.assertEqual(len(data["edges"]), 1)
        self.assertEqual(data["edges"][0]["type"], "knows")
        self.assertEqual(data["edges"][0]["since_session"], 1)

    # ── close-edge + closed_anchor ─────────────────────────────────────────

    def test_close_edge_records_until_session(self):
        for name in ("Aldric", "Mira"):
            _run(["add-node", "--campaign", self.campaign,
                  "--type", "npc", "--name", name], env_overrides=self.env)
        _run(["add-edge", "--campaign", self.campaign,
              "--from", "Aldric", "--to", "Mira",
              "--type", "allied_with", "--since", "1"], env_overrides=self.env)
        edge_id = self._graph_json()["edges"][0]["id"]

        rc, out, err = _run(
            ["close-edge", "--campaign", self.campaign,
             "--id", edge_id, "--at-session", "5"], env_overrides=self.env
        )
        self.assertEqual(rc, 0, msg=err)

        edge = self._graph_json()["edges"][0]
        self.assertEqual(edge["until_session"], 5)
        self.assertNotIn("closed_anchor", edge)  # not provided

    def test_close_edge_records_anchor_when_provided(self):
        """v0.7 schema addition: --anchor stores closed_anchor on the edge."""
        for name in ("Aldric", "Mira"):
            _run(["add-node", "--campaign", self.campaign,
                  "--type", "npc", "--name", name], env_overrides=self.env)
        _run(["add-edge", "--campaign", self.campaign,
              "--from", "Aldric", "--to", "Mira",
              "--type", "serves", "--since", "1"], env_overrides=self.env)
        edge_id = self._graph_json()["edges"][0]["id"]

        anchor = "she walked out of the citadel and never came back"
        rc, out, err = _run(
            ["close-edge", "--campaign", self.campaign,
             "--id", edge_id, "--at-session", "10",
             "--anchor", anchor], env_overrides=self.env
        )
        self.assertEqual(rc, 0, msg=err)

        edge = self._graph_json()["edges"][0]
        self.assertEqual(edge["until_session"], 10)
        self.assertEqual(edge["closed_anchor"], anchor)

    # ── scene-context filtering by session ─────────────────────────────────

    def test_scene_context_excludes_closed_edges_at_later_session(self):
        # Build: Aldric --serves--> Mira (s1, closed s5)
        for name in ("Aldric", "Mira", "Voss"):
            _run(["add-node", "--campaign", self.campaign,
                  "--type", "npc", "--name", name], env_overrides=self.env)
        _run(["add-node", "--campaign", self.campaign,
              "--type", "place", "--name", "Hold"], env_overrides=self.env)
        _run(["add-edge", "--campaign", self.campaign,
              "--from", "Aldric", "--to", "Hold",
              "--type", "lives_in", "--since", "1"], env_overrides=self.env)
        _run(["add-edge", "--campaign", self.campaign,
              "--from", "Aldric", "--to", "Mira",
              "--type", "serves", "--since", "1"], env_overrides=self.env)
        edge_id = self._graph_json()["edges"][1]["id"]
        _run(["close-edge", "--campaign", self.campaign,
              "--id", edge_id, "--at-session", "5"], env_overrides=self.env)

        # Query at session 7 — closed edge should NOT appear
        rc, out, err = _run(
            ["scene-context", "--campaign", self.campaign,
             "--place", "Hold", "--at-session", "7"], env_overrides=self.env
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("Aldric", out)
        # The serves-edge was closed at s5, so at s7 it shouldn't surface
        self.assertNotIn("serves", out,
                         msg=f"expected closed serves-edge to be filtered at s7; got: {out}")

        # Query at session 3 — edge was active, SHOULD appear
        rc, out, err = _run(
            ["scene-context", "--campaign", self.campaign,
             "--place", "Hold", "--at-session", "3"], env_overrides=self.env
        )
        self.assertEqual(rc, 0)
        self.assertIn("serves", out,
                      msg=f"expected serves-edge to be active at s3; got: {out}")

    def test_scene_context_uninitialized_exits_clean(self):
        """If graph.json doesn't exist, scene-context should exit 0 with a notice."""
        rc, out, err = _run(
            ["scene-context", "--campaign", self.campaign,
             "--place", "anywhere"], env_overrides=self.env
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("not initialized", out)

    # ── extract --deterministic end to end ────────────────────────────────

    def test_extract_deterministic_against_synthetic_log(self):
        """Run the actual subcommand: extract --deterministic produces JSON."""
        (self.camp_dir / "session-log.md").write_text(textwrap.dedent("""\
            # Session Log

            ## Session 1

            Aldric met Mira at the docks. Mira swore an oath to Aldric.
        """))
        out_path = self.camp_dir / "proposals.json"
        rc, out, err = _run(
            ["extract", "--campaign", self.campaign,
             "--deterministic", "--write", str(out_path)],
            env_overrides=self.env
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertTrue(out_path.exists())
        proposals = json.loads(out_path.read_text())
        self.assertGreater(len(proposals), 0,
                           f"deterministic extract produced no proposals; stderr: {err}")
        # Every proposal has the required shape
        for p in proposals:
            self.assertIn("from", p)
            self.assertIn("to", p)
            self.assertIn("type", p)
            self.assertIn("source", p)
            self.assertIn("anchor", p["source"])

    # ── extract-apply --pick (existing flow, smoke test) ──────────────────

    def test_extract_apply_pick_subset(self):
        # Build a proposals file directly and run extract-apply
        proposals = [
            {"from": "Aldric", "to": "Mira", "type": "met",
             "since_session": 1,
             "source": {"file": "session-log.md", "session": 1, "anchor": "Aldric met Mira."},
             "confidence": "high"},
            {"from": "Aldric", "to": "Voss", "type": "attacked",
             "since_session": 1,
             "source": {"file": "session-log.md", "session": 1, "anchor": "Aldric attacked Voss."},
             "confidence": "high"},
        ]
        prop_path = self.camp_dir / "props.json"
        prop_path.write_text(json.dumps(proposals))

        rc, out, err = _run(
            ["extract-apply", "--campaign", self.campaign,
             "--proposals", str(prop_path), "--pick", "1"],
            env_overrides=self.env
        )
        self.assertEqual(rc, 0, msg=err)
        edges = self._graph_json()["edges"]
        self.assertEqual(len(edges), 1, msg=f"only proposal #1 should apply; got {edges}")
        self.assertEqual(edges[0]["type"], "met")


    # ── extract --deterministic --apply (one-shot, idempotent) ────────────

    def test_extract_deterministic_apply_is_idempotent(self):
        # A session log with a clean SVO sentence the verb table should catch.
        (self.camp_dir / "session-log.md").write_text(
            "## Session 1\n\nAldric betrayed Mira at the gate.\n"
        )
        rc, out, err = _run(
            ["extract", "--campaign", self.campaign,
             "--deterministic", "--apply", "--min-confidence", "low"],
            env_overrides=self.env
        )
        self.assertEqual(rc, 0, msg=err)
        edges_first = self._graph_json()["edges"]
        self.assertGreater(len(edges_first), 0,
                           f"apply produced no edges; out={out} err={err}")

        # Re-run: dedup must make it a no-op (no duplicate edges).
        rc2, out2, err2 = _run(
            ["extract", "--campaign", self.campaign,
             "--deterministic", "--apply", "--min-confidence", "low"],
            env_overrides=self.env
        )
        self.assertEqual(rc2, 0, msg=err2)
        edges_second = self._graph_json()["edges"]
        self.assertEqual(len(edges_first), len(edges_second),
                         "second apply must be idempotent (no new edges)")

    # ── supersede-edge (Phase 2.5) ──────────────────────────────────────────

    def test_supersede_edge_marks_edge_and_excludes_from_active(self):
        """Phase 2.5: superseded edges stay in the graph but never surface as active."""
        for name in ("Aldric", "Mira"):
            _run(["add-node", "--campaign", self.campaign,
                  "--type", "npc", "--name", name], env_overrides=self.env)
        _run(["add-node", "--campaign", self.campaign,
              "--type", "place", "--name", "Hold"], env_overrides=self.env)
        _run(["add-edge", "--campaign", self.campaign,
              "--from", "Aldric", "--to", "Hold",
              "--type", "lives_in", "--since", "1"], env_overrides=self.env)
        # Wrong edge first: Aldric --serves--> Mira (later retconned)
        _run(["add-edge", "--campaign", self.campaign,
              "--from", "Aldric", "--to", "Mira",
              "--type", "serves", "--since", "1"], env_overrides=self.env)
        # Correct edge: Aldric --opposes--> Mira
        _run(["add-edge", "--campaign", self.campaign,
              "--from", "Aldric", "--to", "Mira",
              "--type", "opposes", "--since", "1"], env_overrides=self.env)

        edges = self._graph_json()["edges"]
        wrong_id = next(e["id"] for e in edges if e["type"] == "serves")
        correct_id = next(e["id"] for e in edges if e["type"] == "opposes")

        rc, out, err = _run(
            ["supersede-edge", "--campaign", self.campaign,
             "--id", wrong_id, "--by", correct_id,
             "--reason", "session-3 retcon: Aldric never served"],
            env_overrides=self.env
        )
        self.assertEqual(rc, 0, msg=err)

        # Read back graph; the wrong edge should have superseded_by set
        wrong_edge = next(e for e in self._graph_json()["edges"] if e["id"] == wrong_id)
        self.assertEqual(wrong_edge["superseded_by"], correct_id)
        self.assertEqual(wrong_edge["supersede_reason"],
                         "session-3 retcon: Aldric never served")

        # scene-context shouldn't surface the superseded edge
        rc, out, err = _run(
            ["scene-context", "--campaign", self.campaign,
             "--place", "Hold", "--at-session", "5"],
            env_overrides=self.env
        )
        self.assertEqual(rc, 0)
        self.assertIn("opposes", out)
        self.assertNotIn("serves", out,
                         msg=f"superseded edge leaked into scene-context: {out}")

    def test_supersede_edge_without_replacement(self):
        """Sometimes a retcon just deletes a relationship without a replacement."""
        for name in ("Aldric", "Mira"):
            _run(["add-node", "--campaign", self.campaign,
                  "--type", "npc", "--name", name], env_overrides=self.env)
        _run(["add-edge", "--campaign", self.campaign,
              "--from", "Aldric", "--to", "Mira",
              "--type", "knows", "--since", "1"], env_overrides=self.env)
        edge_id = self._graph_json()["edges"][0]["id"]

        rc, out, err = _run(
            ["supersede-edge", "--campaign", self.campaign, "--id", edge_id],
            env_overrides=self.env
        )
        self.assertEqual(rc, 0, msg=err)
        edge = self._graph_json()["edges"][0]
        self.assertEqual(edge["superseded_by"], True)

    # ── category-node edges (Phase 2.5) ─────────────────────────────────────

    def test_extract_deterministic_category_object_creates_category_node(self):
        """Phase 2.5: state-verbs with category_object_ok extract category-target edges."""
        (self.camp_dir / "session-log.md").write_text(textwrap.dedent("""\
            # Session Log

            ## Session 1

            Aldric is possessed by a ghost. Mira fears the dark gods.
        """))
        # Run extract --deterministic
        out_path = self.camp_dir / "props.json"
        rc, out, err = _run(
            ["extract", "--campaign", self.campaign,
             "--deterministic", "--write", str(out_path)],
            env_overrides=self.env
        )
        self.assertEqual(rc, 0, msg=err)
        proposals = json.loads(out_path.read_text())
        # We expect at least one categorical proposal
        cat_proposals = [p for p in proposals if p.get("category_to") or p.get("category_from")]
        self.assertGreater(len(cat_proposals), 0,
                           f"no categorical proposals; all proposals: {proposals}")

        # Apply via extract-apply
        rc, out, err = _run(
            ["extract-apply", "--campaign", self.campaign,
             "--proposals", str(out_path)],
            env_overrides=self.env
        )
        self.assertEqual(rc, 0, msg=err)

        graph = self._graph_json()
        cat_nodes = [n for n in graph["nodes"] if n.get("category_node")]
        self.assertGreater(len(cat_nodes), 0,
                           f"no category nodes auto-created; nodes: {[n['id'] for n in graph['nodes']]}")
        # Category node ids should start with cat_
        for n in cat_nodes:
            self.assertTrue(n["id"].startswith("cat_"),
                            f"category node has non-cat_ id: {n['id']}")
            self.assertEqual(n["type"], "category")


if __name__ == "__main__":
    unittest.main()
