#!/usr/bin/env python3
"""
experiment_replay.py — A/B replay harness for measuring graph-context impact.

REFERENCE IMPLEMENTATION. Adapt for your own gap-prone campaign moment.

Reproduces a chosen turn from a campaign frozen at the moment a player asked a
substantive question, then runs Sonnet N times in two prompt conditions:

  baseline:   prompt has state.md, npcs.md, npcs-full.md (target NPC), session-log.md
              (current session truncated at the player's input). NO graph context.

  with-graph: same as baseline + scene-context output from campaign_graph.py
              (which surfaces the relevant edges with source anchors)

Each run is scored heuristically against keyword lists you supply in CONFIG.
Outputs go to OUT_DIR / {baseline,with-graph}{out_suffix} / run_NN.txt
Per-run metrics aggregate into /tmp/replay-summary.json.

USAGE
-----
    python3 experiment_replay.py --n 10 --parallel 4
    python3 experiment_replay.py --n 10 --parallel 4 --sanitize       # strip DM-Style-Notes warnings
    python3 experiment_replay.py --n 10 --parallel 4 --sanitize \\
                                 --out-suffix=-prompted                # for prompt-shape variation runs

CONFIGURE (search for "CONFIGURE:" comments and edit for your moment):
  - CAMP_DIR              campaign data path
  - GRAPH_SCRIPT          path to campaign_graph.py
  - DM_SYSTEM             the system prompt — voice the chosen NPC, response constraints
  - PLAYER_INPUT          verbatim string of the player's frozen-moment input
  - TARGET_NPC_HEADING    regex to extract the NPC's full entry from npcs-full.md
  - SCENE_PRESENT         entity name(s) for `scene-context --present`
  - SCENE_PLACE           place name for `scene-context --place`
  - SCORE_KEYWORDS        dict of score-bucket → keyword lists (see score_run)
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── CONFIGURE ───────────────────────────────────────────────────────────────

# CONFIGURE: your campaign root + graph script path
CAMP_DIR     = pathlib.Path.home() / ".claude/dnd/campaigns/<your-campaign>"
GRAPH_SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "campaign_graph.py"
OUT_DIR      = pathlib.Path("/tmp/replay-outputs")

# CONFIGURE: voice the NPC giving the response. Spell out response constraints
# in the natural voice of that NPC. Keep < ~600 words.
DM_SYSTEM = """You are an atmospheric Dungeon Master running a persistent D&D 5e campaign.
You are voicing the NPC <NAME> responding to the player character <PC>. The player
has just spoken; generate <NAME>'s reply.

<NAME>'s voice: <2-3 sentences of speech-character — pacing, vocabulary, tics>.

<NAME>'s response should cover (in any order, in their natural voice):
1. <First required content beat — e.g. "redirect away from NPC_X — explain why">
2. <Second beat — e.g. "suggest NPC_Y as the right alternative">
3. <Third beat — e.g. "ask the PC to look in on NPC_Z as an errand">

Output ONLY <NAME>'s response — direct dialogue + brief stage-direction beats only.
200-400 words. Do NOT prefix with "<NAME>:" or write meta-narration. Just speak as <NAME>."""

# CONFIGURE: the player's verbatim input that the response must answer
PLAYER_INPUT = '"<verbatim player input that the NPC is responding to>"'

# CONFIGURE: regex matching the target NPC's full entry header in npcs-full.md
TARGET_NPC_HEADING = r"### <NAME>.*?(?=^### |\Z)"

# CONFIGURE: scene-context query parameters
SCENE_PLACE   = "<location-of-the-conversation>"
SCENE_PRESENT = "<NAME>"
SCENE_HOPS    = 2
SCENE_AT_SESSION = 7  # which session number truncate matches

# CONFIGURE: regex to find the in-progress session and truncate it at the
# player's input. The replacement string fills the cut with a brief stage cue.
SESSION_HEADING_PAT = r"## Session 7.*?(?=^## Session |\Z)"
TRUNCATE_PAT = re.compile(
    r'(.*?<verbatim trailing fragment of player input>)\.*$',
    re.DOTALL,
)
TRUNCATE_REPLACE_TAIL = "\n\n[<NAME> has not yet responded. The room is quiet.]"

# CONFIGURE: score-keyword lists. score_run() returns a dict of bucket-name →
# (verdict, matched_phrase). Customize for the gap you're testing.
SCORE_KEYWORDS = {
    "gap_mode_phrases": [
        # Phrases that would indicate the model treats a known entity as a fresh contact
        "tell him i sent you", "give him my name", "use my name",
        "introduce yourself", "let him know who",
    ],
    "explicit_acknowledgment_phrases": [
        # Phrases that surface the existing relationship
        "go back to", "your old contact", "the one who sent",
        "we have a working relationship", "since you",
    ],
    "redirect_target_keyword": "<offlimits-NPC-name>",
    "redirect_phrases": [
        "wrong door", "don't go", "not directly", "stay away",
        "won't help", "dangerous to meet",
    ],
    "alternative_target_keyword": "<right-door-NPC-name>",
}

# ── methodology ─────────────────────────────────────────────────────────────


def sanitize_state(state_text: str) -> str:
    """Strip DM Style Notes + any gap-warnings for original-failure-mode replay."""
    out = state_text
    out = re.sub(r"## DM Style Notes\b.*?(?=^## |\Z)", "", out,
                 flags=re.MULTILINE | re.DOTALL)
    # CONFIGURE: add additional gap-specific warning patterns to strip here
    out = re.sub(r"^.*continuity gap.*$\n?", "", out, flags=re.MULTILINE | re.IGNORECASE)
    return out


def sanitize_session(session_text: str) -> str:
    """Strip any gap meta-commentary inserted into the session narrative."""
    # CONFIGURE: add patterns matching meta-DM annotations you've placed
    out = re.sub(r"\*\(continuity gap noted:.*?\)\*", "", session_text,
                 flags=re.MULTILINE | re.DOTALL)
    return out


def truncate_current_session(session_log_text: str) -> str:
    """Cut session-log.md at the player's frozen input."""
    sess_match = re.search(SESSION_HEADING_PAT, session_log_text,
                           re.MULTILINE | re.DOTALL)
    if not sess_match:
        return session_log_text
    sess = sess_match.group(0)
    return TRUNCATE_PAT.sub(r"\1" + TRUNCATE_REPLACE_TAIL, sess)


def extract_target_npc(npcs_full_text: str) -> str:
    m = re.search(TARGET_NPC_HEADING, npcs_full_text, re.MULTILINE | re.DOTALL)
    return m.group(0) if m else ""


def get_graph_context(campaign: str) -> str:
    """Run scene-context against the live campaign (with the graph applied)."""
    result = subprocess.run(
        [
            sys.executable, str(GRAPH_SCRIPT), "scene-context",
            "--campaign", campaign,
            "--place", SCENE_PLACE,
            "--present", SCENE_PRESENT,
            "--hops", str(SCENE_HOPS),
            "--at-session", str(SCENE_AT_SESSION),
        ],
        capture_output=True, encoding="utf-8", text=True, timeout=30,
    )
    return result.stdout


def build_prompt(state, npcs, npc_full, session_truncated, graph_ctx=None):
    parts = [
        "CAMPAIGN STATE (state.md — full):", state,
        "\n\nNPC INDEX (npcs.md):", npcs,
        "\n\nNPC FULL ENTRY (npcs-full.md):", npc_full,
    ]
    if graph_ctx:
        parts += [
            "\n\nRELATIONSHIP GRAPH (active edges in current scene "
            "— authoritative for who-relates-to-whom):",
            graph_ctx,
        ]
    parts += [
        "\n\nCURRENT SESSION (session-log.md — narrative up to player input):",
        session_truncated,
        "\n\nPLAYER INPUT:", PLAYER_INPUT,
        "\n\nGenerate the NPC's reply. Stay in voice. No DM meta-commentary."
        + (" Consult the relationship graph before referencing any NPC." if graph_ctx else ""),
    ]
    return "\n\n".join(parts)


def call_sonnet(prompt: str, system: str, timeout: int = 180) -> tuple:
    t0 = time.time()
    result = subprocess.run(
        [
            "claude", "-p",
            "--model", "claude-sonnet-4-6",
            "--system-prompt", system,
            prompt,
        ],
        capture_output=True, encoding="utf-8", text=True, timeout=timeout,
    )
    elapsed = time.time() - t0
    if result.returncode != 0:
        return "", elapsed, result.stderr[:200]
    return result.stdout.strip(), elapsed, ""


# ── scoring ─────────────────────────────────────────────────────────────────


def _first_match(text: str, phrases: list) -> str:
    return next((p for p in phrases if p in text), "")


def score_run(text: str) -> dict:
    """Heuristic scoring against SCORE_KEYWORDS. Returns per-bucket verdicts."""
    t = text.lower()
    K = SCORE_KEYWORDS
    return {
        "gap_mode_match":     _first_match(t, K.get("gap_mode_phrases", [])),
        "explicit_ack_match": _first_match(t, K.get("explicit_acknowledgment_phrases", [])),
        "mentions_offlimits": K.get("redirect_target_keyword", "").lower() in t,
        "redirect_phrase":    _first_match(t, K.get("redirect_phrases", [])),
        "mentions_alt":       K.get("alternative_target_keyword", "").lower() in t,
        "length_chars":       len(text),
        "length_words":       len(text.split()),
    }


# ── main ────────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10, help="runs per condition")
    ap.add_argument("--parallel", type=int, default=4, help="concurrent claude calls")
    ap.add_argument("--conditions", nargs="+", default=["baseline", "with-graph"])
    ap.add_argument("--sanitize", action="store_true",
                    help="Strip DM Style Notes + gap-warnings for original-failure-mode replay")
    ap.add_argument("--out-suffix", default="",
                    help="suffix appended to output dirs (e.g. =-prompted)")
    ap.add_argument("--campaign", default=None,
                    help="campaign name (overrides CAMP_DIR basename for graph scene-context)")
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    for c in args.conditions:
        (OUT_DIR / (c + args.out_suffix)).mkdir(exist_ok=True)

    campaign = args.campaign or CAMP_DIR.name

    # Load campaign data
    state = (CAMP_DIR / "state.md").read_text(encoding="utf-8", errors="replace")
    npcs = (CAMP_DIR / "npcs.md").read_text(encoding="utf-8", errors="replace")
    npcs_full = ((CAMP_DIR / "npcs-full.md").read_text(encoding="utf-8", errors="replace")
                 if (CAMP_DIR / "npcs-full.md").exists() else "")
    npc_full = extract_target_npc(npcs_full) or "(target NPC entry not found in npcs-full.md)"

    session_log = (CAMP_DIR / "session-log.md").read_text(encoding="utf-8", errors="replace")
    session_truncated = truncate_current_session(session_log)

    if args.sanitize:
        state = sanitize_state(state)
        session_truncated = sanitize_session(session_truncated)
        print(f"[sanitize] stripped DM Style Notes + gap-warnings ({len(state)} chars after)",
              file=sys.stderr)

    # Build prompts
    baseline_prompt = build_prompt(state, npcs, npc_full, session_truncated)
    with_graph_prompt = None
    if "with-graph" in args.conditions:
        graph_ctx = get_graph_context(campaign)
        print(f"[graph] scene-context produced {len(graph_ctx)} chars", file=sys.stderr)
        with_graph_prompt = build_prompt(state, npcs, npc_full, session_truncated,
                                         graph_ctx=graph_ctx)

    print(f"[setup] baseline prompt: {len(baseline_prompt)} chars", file=sys.stderr)
    if with_graph_prompt:
        print(f"[setup] with-graph prompt: {len(with_graph_prompt)} chars", file=sys.stderr)

    # Submit jobs
    jobs = []
    for cond in args.conditions:
        prompt = with_graph_prompt if cond == "with-graph" else baseline_prompt
        for i in range(args.n):
            jobs.append((cond, i, prompt))

    print(f"[run] {len(jobs)} jobs across {args.conditions}, parallel={args.parallel}",
          file=sys.stderr)

    results = []
    with ThreadPoolExecutor(max_workers=args.parallel) as ex:
        futures = {ex.submit(call_sonnet, prompt, DM_SYSTEM): (cond, i)
                   for cond, i, prompt in jobs}
        for fut in as_completed(futures):
            cond, i = futures[fut]
            try:
                output, elapsed, err = fut.result()
            except Exception as e:
                output, elapsed, err = "", 0, str(e)
            out_subdir = cond + args.out_suffix
            (OUT_DIR / out_subdir / f"run_{i:02d}.txt").write_text(
                output or f"[ERROR: {err}]")
            score = score_run(output) if output else {"error": err}
            results.append({"condition": cond, "run": i, "elapsed": elapsed,
                            "score": score, "output_chars": len(output)})
            print(f"  [{out_subdir}/run_{i:02d}] {elapsed:.0f}s {len(output)}ch  "
                  f"gap={bool(score.get('gap_mode_match'))} "
                  f"ack={bool(score.get('explicit_ack_match'))} "
                  f"redirect={bool(score.get('redirect_phrase'))}",
                  file=sys.stderr)

    pathlib.Path("/tmp/replay-summary.json").write_text(
        json.dumps({"results": results, "n_per_condition": args.n}, indent=2))

    # Summarize
    print("\n=== summary ===")
    for cond in args.conditions:
        cond_results = [r for r in results if r["condition"] == cond]
        n = max(1, len(cond_results))
        gap_pct      = sum(1 for r in cond_results if r["score"].get("gap_mode_match")) / n * 100
        ack_pct      = sum(1 for r in cond_results if r["score"].get("explicit_ack_match")) / n * 100
        redirect_pct = sum(1 for r in cond_results if r["score"].get("redirect_phrase")) / n * 100
        alt_pct      = sum(1 for r in cond_results if r["score"].get("mentions_alt")) / n * 100
        avg_words    = sum(r["score"].get("length_words", 0) for r in cond_results) / n
        print(f"\n  {cond} (n={len(cond_results)}):")
        print(f"    gap-mode language:           {gap_pct:.0f}%")
        print(f"    explicit acknowledgment:     {ack_pct:.0f}%")
        print(f"    correct redirect:            {redirect_pct:.0f}%")
        print(f"    mentions alternative target: {alt_pct:.0f}%")
        print(f"    avg word count:              {avg_words:.0f}")


if __name__ == "__main__":
    main()
