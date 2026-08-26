#!/usr/bin/env python3
"""
dm_help.py — On-demand DM hint generator.

Called by Flask /help-request endpoint as a subprocess.
Reads recent display log + campaign state.md + current session-log.md entry,
calls the Claude API, sends result to the display via send.py --tutor.

Context hierarchy (most → least current):
  1. text_log.json   — real-time scene (last N display blocks)
  2. session-log.md  — current session events and open threads (authoritative for in-session state)
  3. state.md        — campaign-level persistent context (targeted sections only; may lag)
  4. arc context     — current beat's consequence (DM-only; shapes hint tone, never revealed)

Lock lifecycle:
  Flask creates .help-lock (O_EXCL) before spawning this process.
  This script removes .help-lock in its finally block.
  Multiple browser clicks → Flask returns 409 on all but the first.
"""

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))
from paths import find_campaign as _find_campaign, skill_root as _skill_root, runtime_dir as _runtime_dir

BASE      = _skill_root()
_RT       = _runtime_dir()
LOCK_FILE = _RT / ".help-lock"            # runtime state → update-safe dir
LOG_FILE  = _RT / "text_log.json"         # runtime state → update-safe dir
SEND_PY   = BASE / "display" / "send.py"  # bundled code → skill dir

# Sections to extract from state.md.
# Deliberately excludes "## Open Threads & Rumours" and "## Recent Events"
# because those go stale during a session — session-log.md is more current.
STATE_SECTIONS = [
    "## Current Situation",
    "## Active Quests",
    "## World State",
    "## Session Flags",
]
STATE_SECTION_LINE_LIMIT = 20  # per section — keeps prompt tight


def release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def get_recent_display(n: int = 10) -> str:
    """Return the last n display blocks as labelled text, skipping previous tutor blocks."""
    if not LOG_FILE.exists():
        return ""
    try:
        data = json.loads(LOG_FILE.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return ""
    recent = data[-n:] if len(data) >= n else data
    parts = []
    for item in recent:
        if not isinstance(item, dict) or "text" not in item:
            continue
        if item.get("tutor"):
            continue  # don't feed prior hints back as scene context
        text = item["text"].strip()
        if item.get("player"):
            parts.append(f"[PLAYER ACTION] {text}")
        elif item.get("npc"):
            parts.append(f"[NPC: {item.get('npc', '')}] {text}")
        elif item.get("dice"):
            parts.append(f"[DICE] {text}")
        else:
            parts.append(f"[DM] {text}")
    return "\n\n".join(parts)


def get_campaign_state(campaign: str) -> str:
    """
    Extract targeted sections from state.md.
    Skips Open Threads and Recent Events — those go stale mid-session.
    session-log.md is the authoritative source for in-session state.
    """
    state_path = _find_campaign(campaign) / "state.md"
    if not state_path.exists():
        return ""
    text = state_path.read_text(encoding="utf-8", errors="replace")
    parts = []
    for header in STATE_SECTIONS:
        match = re.search(
            rf"(^{re.escape(header)}.*?)(?=^## |\Z)",
            text,
            re.MULTILINE | re.DOTALL,
        )
        if match:
            lines = match.group(1).strip().splitlines()[:STATE_SECTION_LINE_LIMIT]
            parts.append("\n".join(lines))
    return "\n\n".join(parts)


def get_graph_context(campaign: str) -> str:
    """
    Pull a focused subgraph from the campaign graph for the current scene.

    Reads state.md for current location and session count, then shells out to
    campaign_graph.py scene-context. Returns the subgraph text, or "" when the
    graph isn't initialized or location can't be resolved.

    Sandbox / experimental: degrades silently — never blocks hint generation.
    """
    state_path = _find_campaign(campaign) / "state.md"
    if not state_path.exists():
        return ""
    text = state_path.read_text(encoding="utf-8", errors="replace")
    loc_match = re.search(r"^- \*\*Location:\*\*\s*(.+)$", text, re.MULTILINE)
    sess_match = re.search(r"\*\*Session count:\*\*\s*(\d+)", text)
    if not loc_match:
        return ""
    location = loc_match.group(1).strip()
    if not location or location.startswith("<"):
        return ""
    cmd = [
        sys.executable,
        str(pathlib.Path(__file__).resolve().parent.parent / "scripts" / "campaign_graph.py"),
        "scene-context",
        "--campaign", campaign,
        "--place", location,
        "--hops", "2",
    ]
    if sess_match:
        cmd += ["--at-session", sess_match.group(1)]
    try:
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", text=True, timeout=5)
    except Exception:
        return ""
    out = (result.stdout or "").strip()
    if not out or "graph not initialized" in out:
        return ""
    return out


def get_arc_context(campaign: str) -> str:
    """
    Extract the current beat's 'what_changes' from the Campaign Arc YAML block in state.md.
    Returns a one-line thematic summary of what consequence is building — for the hint
    model to shape tone toward readiness, not prevention.
    Returns empty string if arc section is missing or unparseable.
    """
    state_path = _find_campaign(campaign) / "state.md"
    if not state_path.exists():
        return ""

    text = state_path.read_text(encoding="utf-8", errors="replace")

    # Extract the YAML block inside ## Campaign Arc
    arc_match = re.search(
        r"^## Campaign Arc\s*```yaml(.*?)```",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not arc_match:
        return ""

    yaml_text = arc_match.group(1)

    # Pull current_beat id
    beat_id_match = re.search(r"^current_beat:\s*[\"']?(\S+?)[\"']?\s*$", yaml_text, re.MULTILINE)
    if not beat_id_match:
        return ""
    current_beat_id = beat_id_match.group(1).strip("\"'")

    # Find the beat block with that id — look for "id: <current_beat_id>"
    # then scan forward for what_changes
    beat_block_match = re.search(
        rf"- id:\s*[\"']?{re.escape(current_beat_id)}[\"']?(.*?)(?=\s*- id:|\Z)",
        yaml_text,
        re.DOTALL,
    )
    if not beat_block_match:
        return ""

    beat_block = beat_block_match.group(1)

    # Extract what_changes — may be multi-line with leading spaces
    wc_match = re.search(r'what_changes:\s*["\']?(.*?)(?=["\']?\s*\w+:|$)', beat_block, re.DOTALL)
    if not wc_match:
        return ""

    what_changes = wc_match.group(1).strip().strip("\"'")
    # Collapse whitespace from multi-line YAML values
    what_changes = re.sub(r"\s+", " ", what_changes).strip()

    if not what_changes:
        return ""

    # Also extract world_pressure — the mechanism the beat arrives through.
    # This tells the hint what the party can actually engage with.
    wp_match = re.search(r'world_pressure:\s*["\']?(.*?)(?=["\']?\s*\w+:|$)', beat_block, re.DOTALL)
    world_pressure = ""
    if wp_match:
        world_pressure = re.sub(r"\s+", " ", wp_match.group(1).strip().strip("\"'")).strip()

    # Also extract label for context
    label_match = re.search(r'label:\s*["\']?(.*?)["\']?\s*$', beat_block, re.MULTILINE)
    label = label_match.group(1).strip() if label_match else ""

    lines = [
        "Current story beat (DM only — never quote or reference directly):",
    ]
    if label:
        lines.append(f"  Beat label: {label}")
    lines.append(f"  What must change: {what_changes}")
    if world_pressure:
        lines.append(f"  How it arrives (the node the party can engage with): {world_pressure}")
    lines.append(
        "Use this to: (1) shape hint tone toward preparation, not urgency to prevent; "
        "(2) surface the specific pressures or decisions that matter before this lands — "
        "hint at the kind of positioning, alliances, or information that will matter when it does."
    )

    return "\n".join(lines)


def get_session_context(campaign: str) -> str:
    """
    Extract the most recent session entry from session-log.md.
    This is the authoritative source for what has actually happened in the
    current session — more current than state.md during an active session.
    Falls back to the archive if the main log is empty or has no sessions.
    """
    log_path = _find_campaign(campaign) / "session-log.md"
    if not log_path.exists():
        return ""
    text = log_path.read_text(encoding="utf-8", errors="replace")

    # Find all session headers — "## Session N" or "## Session N — ..."
    matches = list(re.finditer(r"^## Session \d+", text, re.MULTILINE))
    if not matches:
        return ""

    # Take the last (most recent) session
    last_start = matches[-1].start()
    session_text = text[last_start:]

    # Hard limit: 100 lines is enough for Key Events + Open Threads
    lines = session_text.splitlines()[:100]
    return "\n".join(lines)


def call_claude(display: str, state: str, session: str, arc: str, graph: str = "") -> str:
    """Call claude -p (non-interactive print mode) — uses Claude Code's own auth."""
    system = (
        "You are a D&D 5e Dungeon Master generating a brief in-character DM hint. "
        "You are given three sources of context in decreasing order of freshness: "
        "(1) RECENT SCENE — the last few display blocks, most current; "
        "(2) CURRENT SESSION — key events and open threads logged this session, authoritative "
        "for what has actually happened; "
        "(3) CAMPAIGN STATE — persistent campaign context, may lag behind current session events. "
        "If sources conflict, trust RECENT SCENE first, then CURRENT SESSION, then CAMPAIGN STATE. "
        "Based on this context, identify the single most useful thing the player may not have "
        "considered right now: a skill check worth attempting and what it would reveal; "
        "2-3 visible options at this decision point noting which close doors permanently; "
        "if there is an irreversible risk begin with: ⚠ WARNING:; "
        "or an unused class feature or reaction relevant to this exact moment. "
        "Rules: 2-4 sentences maximum. Write from inside the fiction — no rule names, "
        "no meta-language. Never reveal information the character could not know. "
        "If there is genuinely nothing useful to add, respond with exactly: SKIP"
        "\n\n"
        "ARC TONE INSTRUCTION (DM-only — never name or quote this to the player): "
        "You are also given the thematic consequence that the story is building toward. "
        "Do not reveal it, reference it, or hint that it can be prevented. "
        "Instead, let it shape the emotional register of your hint: nudge the player toward "
        "positioning and preparation rather than urgency to stop something. "
        "The question to plant is not 'how do I prevent this?' but 'what do I need in place "
        "when this changes?' A hint that does this well feels like atmosphere, not a warning — "
        "the difference between 'sometimes plans don't outrun the world' and 'hurry, stop X'."
    )

    prompt_parts = []
    if arc:
        prompt_parts.append(f"ARC CONTEXT (DM-only — shape tone only, never reveal):\n{arc}")
    if state:
        prompt_parts.append(f"CAMPAIGN STATE:\n{state}")
    if graph:
        prompt_parts.append(f"RELATIONSHIP GRAPH (active edges in current scene):\n{graph}")
    if session:
        prompt_parts.append(f"CURRENT SESSION (authoritative — trust over campaign state):\n{session}")
    if display:
        prompt_parts.append(f"RECENT SCENE (most current — trust over all other sources):\n{display}")
    prompt_parts.append("Generate a DM hint for the player's current situation.")

    prompt = "\n\n".join(prompt_parts)

    result = subprocess.run(
        [
            "claude", "-p",
            "--model", "claude-sonnet-4-6",
            "--system-prompt", system,
            prompt,
        ],
        capture_output=True,
        encoding="utf-8", text=True,
        timeout=60,
    )

    if result.returncode != 0:
        return "SKIP"

    return result.stdout.strip()


def send_tutor(text: str) -> None:
    subprocess.run(
        [sys.executable, str(SEND_PY), "--tutor"],
        input=text,
        encoding="utf-8", text=True,
        capture_output=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and send an on-demand DM hint.")
    parser.add_argument("--campaign", required=True, help="Campaign directory name")
    args = parser.parse_args()

    try:
        display = get_recent_display(10)
        state   = get_campaign_state(args.campaign)
        session = get_session_context(args.campaign)
        arc     = get_arc_context(args.campaign)
        graph   = get_graph_context(args.campaign)

        if not display and not state and not session:
            return

        hint = call_claude(display, state, session, arc, graph)
        if hint.strip().upper() == "SKIP":
            return

        send_tutor(hint)
    finally:
        release_lock()


if __name__ == "__main__":
    main()
