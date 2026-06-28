"""
campaign_graph.py — typed-edge relationship graph over campaign nodes.

Subcommand `extract` (sandbox addition) — Haiku pass over session-log-archive.md
+ session-log.md to propose backstory edges with source-anchor provenance. Writes
proposals to stdout (and optionally to a JSON file) for DM review. Apply with
`extract-apply --proposals FILE [--pick N1,N2,...]`.


Sandbox experiment (Phase 1). Supplements markdown — npcs-full.md and
session-log.md remain authoritative. Graph is an *index over* canonical
sources used to extract scene-relevant subgraphs at lower token cost than
loading the full npcs.md index at /dnd load.

Storage: <campaign-dir>/graph.json
  {
    "version": 1,
    "nodes": [
      {"id": "npc_velkyn", "type": "npc", "name": "Velkyn", "tags": [...], "summary": "..."}
    ],
    "edges": [
      {"id": "e1", "from": "<id>", "to": "<id>", "type": "loyal_to",
       "since_session": 1, "until_session": null, "note": "..."}
    ]
  }

Node types (open vocab, suggested): npc, faction, place, item, thread.
Edge types (open vocab, common): loyal_to, opposes, allied_with, member_of,
  lives_in, controls, knows_about, friends_with, lover_of, owes, rules,
  related_by_blood, advances_thread, blocks_thread.

Edges are time-stamped. An edge is "active at session N" iff:
  since_session <= N AND (until_session is null OR until_session > N).
Use close-edge to set until_session when a relationship ends.

Usage:
  python3 campaign_graph.py <subcommand> --campaign <name> [args]

Subcommands:
  add-node       --type T --name N [--id ID] [--tags t1,t2] [--summary S]
  add-edge       --from FROM --to TO --type T [--since N] [--until N] [--note S]
  close-edge     --id EDGE_ID [--at-session N]
  list           [--type T] [--at-session N]
  show           --id ID
  scene-context  --place ID [--present ID,ID] [--threads ID,ID] [--hops H]
                 [--at-session N]
  subgraph       --seed ID [--seed ID ...] [--hops H] [--at-session N]
"""
import argparse
import json
import pathlib
import sys
from typing import Optional

from paths import find_campaign


# -------- IO --------

def _graph_path(campaign: str):
    return find_campaign(campaign) / "graph.json"


def _load(campaign: str) -> dict:
    p = _graph_path(campaign)
    if not p.exists():
        return {"version": 1, "nodes": [], "edges": []}
    with open(p) as f:
        data = json.load(f)
    data.setdefault("version", 1)
    data.setdefault("nodes", [])
    data.setdefault("edges", [])
    return data


def _save(campaign: str, data: dict) -> None:
    p = _graph_path(campaign)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# -------- helpers --------

def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s.lower()).strip("_")


def _next_edge_id(edges: list) -> str:
    n = 1
    existing = {e["id"] for e in edges if e.get("id", "").startswith("e")}
    while f"e{n}" in existing:
        n += 1
    return f"e{n}"


def _node_by_id(data: dict, node_id: str) -> Optional[dict]:
    for n in data["nodes"]:
        if n["id"] == node_id:
            return n
    return None


def _resolve_node(data: dict, ref: str) -> Optional[str]:
    """Resolve a user-supplied ref to a node id. Tries exact id, then
    case-insensitive name, then name prefix. Raises ValueError on ambiguity."""
    if not ref:
        return None
    if _node_by_id(data, ref):
        return ref
    ref_low = ref.lower()
    exact = [n for n in data["nodes"] if n.get("name", "").lower() == ref_low]
    if len(exact) == 1:
        return exact[0]["id"]
    if len(exact) > 1:
        ids = ", ".join(n["id"] for n in exact)
        raise ValueError(f"name '{ref}' matches multiple nodes: {ids}. use id directly.")
    prefix = [n for n in data["nodes"] if n.get("name", "").lower().startswith(ref_low)]
    if len(prefix) == 1:
        return prefix[0]["id"]
    if len(prefix) > 1:
        ids = ", ".join(n["id"] for n in prefix)
        raise ValueError(f"name prefix '{ref}' matches multiple nodes: {ids}. use id directly.")
    return None


def _resolve_or_die(data: dict, ref: str, label: str) -> str:
    try:
        node_id = _resolve_node(data, ref)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    if node_id is None:
        print(f"error: {label} '{ref}' not found (no id or name match).", file=sys.stderr)
        sys.exit(1)
    return node_id


def _resolve_csv(data: dict, csv: str, label: str) -> list:
    if not csv:
        return []
    return [_resolve_or_die(data, ref.strip(), label) for ref in csv.split(",") if ref.strip()]


def _edge_by_id(data: dict, edge_id: str) -> Optional[dict]:
    for e in data["edges"]:
        if e.get("id") == edge_id:
            return e
    return None


def _edge_active_at(edge: dict, session: Optional[int]) -> bool:
    """Is the edge active at the given session?

    Returns False if the edge was superseded (hard retcon) — superseded edges
    stay in the graph for audit trail but never surface as 'active' state.
    """
    if edge.get("superseded_by"):
        return False
    if session is None:
        return edge.get("until_session") is None
    since = edge.get("since_session", 0) or 0
    until = edge.get("until_session")
    if since > session:
        return False
    if until is not None and until <= session:
        return False
    return True


# -------- subcommands --------

def cmd_add_node(args) -> int:
    data = _load(args.campaign)
    node_id = args.id or f"{args.type}_{_slug(args.name)}"
    if _node_by_id(data, node_id):
        print(f"error: node id '{node_id}' already exists. use --id to override.",
              file=sys.stderr)
        return 1
    node = {
        "id": node_id,
        "type": args.type,
        "name": args.name,
    }
    if args.tags:
        node["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.summary:
        node["summary"] = args.summary
    data["nodes"].append(node)
    _save(args.campaign, data)
    print(f"added node {node_id}  ({args.type}: {args.name})")
    return 0


def cmd_add_edge(args) -> int:
    data = _load(args.campaign)
    from_id = _resolve_or_die(data, args.from_id, "source")
    to_id = _resolve_or_die(data, args.to_id, "target")
    edge = {
        "id": _next_edge_id(data["edges"]),
        "from": from_id,
        "to": to_id,
        "type": args.type,
        "since_session": args.since,
        "until_session": args.until,
    }
    if args.note:
        edge["note"] = args.note
    data["edges"].append(edge)
    _save(args.campaign, data)
    sess = f" since:{args.since}" if args.since is not None else ""
    print(f"added edge {edge['id']}  {from_id} --[{args.type}]--> {to_id}{sess}")
    return 0


def cmd_close_edge(args) -> int:
    data = _load(args.campaign)
    edge = _edge_by_id(data, args.id)
    if not edge:
        print(f"error: edge '{args.id}' not found.", file=sys.stderr)
        return 1
    if edge.get("until_session") is not None:
        print(f"warning: edge {args.id} was already closed at session "
              f"{edge['until_session']}; overwriting.", file=sys.stderr)
    edge["until_session"] = args.at_session
    if getattr(args, "anchor", None):
        edge["closed_anchor"] = args.anchor
    _save(args.campaign, data)
    msg = f"closed edge {args.id} at session {args.at_session}"
    if getattr(args, "anchor", None):
        msg += f' — "{args.anchor[:60]}"'
    print(msg)
    return 0


def cmd_supersede_edge(args) -> int:
    """Mark an edge as superseded by another (hard retcon).

    Use when canon explicitly contradicts a prior extraction — e.g. a session
    log was corrected, or a relationship was misinterpreted. The old edge
    stays in the graph for audit trail; scene-context filters it out, but
    historical / subgraph queries can still surface it.

    Distinct from `close-edge`: close-edge ends a state cleanly (the relationship
    was real, then ended). supersede-edge says the original edge was wrong.
    """
    data = _load(args.campaign)
    wrong = _edge_by_id(data, args.id)
    if not wrong:
        print(f"error: edge '{args.id}' not found.", file=sys.stderr)
        return 1
    correct = _edge_by_id(data, args.by) if args.by else None
    if args.by and not correct:
        print(f"error: superseding edge '{args.by}' not found.", file=sys.stderr)
        return 1
    if args.by:
        wrong["superseded_by"] = args.by
    else:
        wrong["superseded_by"] = True  # contradicted with no replacement
    if getattr(args, "reason", None):
        wrong["supersede_reason"] = args.reason
    _save(args.campaign, data)
    target = f"by edge {args.by}" if args.by else "(no replacement)"
    print(f"marked edge {args.id} as superseded {target}")
    return 0


def cmd_list(args) -> int:
    data = _load(args.campaign)
    nodes = data["nodes"]
    if args.type:
        nodes = [n for n in nodes if n.get("type") == args.type]
    nodes_sorted = sorted(nodes, key=lambda n: (n.get("type", ""), n.get("name", "")))
    print(f"# {args.campaign} graph — {len(data['nodes'])} nodes, "
          f"{len(data['edges'])} edges")
    if args.at_session is not None:
        active = [e for e in data["edges"] if _edge_active_at(e, args.at_session)]
        print(f"# active edges at session {args.at_session}: {len(active)}")
    print()
    def _plural(t: str) -> str:
        if t and t.endswith("y"):
            return t[:-1] + "ies"
        return (t or "?") + "s"
    cur_type = None
    for n in nodes_sorted:
        if n.get("type") != cur_type:
            cur_type = n.get("type")
            print(f"## {_plural(cur_type)}")
        tags = " [" + ",".join(n.get("tags", [])) + "]" if n.get("tags") else ""
        print(f"  {n['id']}  {n['name']}{tags}")
    return 0


def cmd_show(args) -> int:
    data = _load(args.campaign)
    node_id = _resolve_or_die(data, args.id, "node")
    n = _node_by_id(data, node_id)
    print(f"{n['id']}  ({n.get('type', '?')})  {n.get('name', '')}")
    if n.get("tags"):
        print(f"  tags: {', '.join(n['tags'])}")
    if n.get("summary"):
        print(f"  summary: {n['summary']}")
    print()
    incoming = [e for e in data["edges"] if e["to"] == node_id]
    outgoing = [e for e in data["edges"] if e["from"] == node_id]
    if outgoing:
        print("outgoing:")
        for e in outgoing:
            _print_edge(e, data, direction="out")
    if incoming:
        print("incoming:")
        for e in incoming:
            _print_edge(e, data, direction="in")
    return 0


def _print_edge(e: dict, data: dict, direction: str = "out") -> None:
    other_id = e["to"] if direction == "out" else e["from"]
    other = _node_by_id(data, other_id)
    other_name = other["name"] if other else other_id
    arrow = "-->" if direction == "out" else "<--"
    sess = []
    if e.get("since_session") is not None:
        sess.append(f"since s{e['since_session']}")
    if e.get("until_session") is not None:
        sess.append(f"until s{e['until_session']}")
    sess_str = " (" + ", ".join(sess) + ")" if sess else ""
    note = f"  — {e['note']}" if e.get("note") else ""
    print(f"  [{e.get('id', '?')}] {arrow} {e['type']}: {other_name}{sess_str}{note}")


def cmd_subgraph(args) -> int:
    data = _load(args.campaign)
    if not data["nodes"]:
        print(f"# graph not initialized for campaign '{args.campaign}' — skipping.")
        return 0
    seeds = [_resolve_or_die(data, s, "seed") for s in args.seed if s]
    sub = _expand(data, seeds, args.hops, args.at_session)
    _emit_subgraph(sub, args.at_session)
    return 0


def cmd_scene_context(args) -> int:
    data = _load(args.campaign)
    if not data["nodes"]:
        # Graph not yet initialized for this campaign. Print a brief notice and
        # exit 0 so this is safe to call unconditionally during /dnd load.
        print(f"# graph not initialized for campaign '{args.campaign}' — skipping scene-context.")
        return 0
    seeds: list = []
    if args.place:
        seeds.append(_resolve_or_die(data, args.place, "place"))
    if args.present:
        seeds.extend(_resolve_csv(data, args.present, "present"))
    if args.threads:
        seeds.extend(_resolve_csv(data, args.threads, "thread"))
    if not seeds:
        print("error: scene-context needs at least --place, --present, or --threads.",
              file=sys.stderr)
        return 1
    sub = _expand(data, seeds, args.hops, args.at_session)
    print(f"# scene context — seeds: {', '.join(seeds)}, hops: {args.hops}"
          + (f", at session {args.at_session}" if args.at_session is not None else ""))
    print()
    _emit_subgraph(sub, args.at_session)
    return 0


def _expand(data: dict, seeds: list[str], hops: int,
            at_session: Optional[int]) -> dict:
    """BFS from seeds, hops bounded, only traversing edges active at at_session."""
    visited_nodes = set(seeds)
    frontier = set(seeds)
    visited_edges: list[dict] = []
    edges_by_node: dict[str, list[dict]] = {}
    for e in data["edges"]:
        if at_session is not None and not _edge_active_at(e, at_session):
            continue
        edges_by_node.setdefault(e["from"], []).append(e)
        edges_by_node.setdefault(e["to"], []).append(e)
    for _ in range(hops):
        next_frontier = set()
        for node_id in frontier:
            for e in edges_by_node.get(node_id, []):
                if e not in visited_edges:
                    visited_edges.append(e)
                other = e["to"] if e["from"] == node_id else e["from"]
                if other not in visited_nodes:
                    next_frontier.add(other)
                    visited_nodes.add(other)
        frontier = next_frontier
        if not frontier:
            break
    nodes = [n for n in data["nodes"] if n["id"] in visited_nodes]
    return {"nodes": nodes, "edges": visited_edges}


def _emit_subgraph(sub: dict, at_session: Optional[int]) -> None:
    by_type: dict[str, list[dict]] = {}
    for n in sub["nodes"]:
        by_type.setdefault(n.get("type", "?"), []).append(n)

    def _label(n: dict) -> str:
        # Category nodes display with an "(unnamed)" marker so the GM
        # remembers the player canonically doesn't have a name yet.
        if n.get("category_node"):
            return f"{n['name']} (unnamed)"
        return n["name"]

    # English-ish pluralization for the section header. Most node types end
    # in a consonant where "+s" is fine; "category" wants "ies".
    def _plural(t: str) -> str:
        if t.endswith("y"):
            return t[:-1] + "ies"
        return t + "s"
    for t in sorted(by_type):
        print(f"## {_plural(t)} ({len(by_type[t])})")
        for n in sorted(by_type[t], key=lambda x: x.get("name", "")):
            tags = " [" + ",".join(n.get("tags", [])) + "]" if n.get("tags") else ""
            summary = f" — {n['summary']}" if n.get("summary") else ""
            print(f"  {n['id']}  {_label(n)}{tags}{summary}")
        print()
    if sub["edges"]:
        print(f"## relationships ({len(sub['edges'])})")
        node_label = {n["id"]: _label(n) for n in sub["nodes"]}
        for e in sub["edges"]:
            f_name = node_label.get(e["from"], e["from"])
            t_name = node_label.get(e["to"], e["to"])
            sess = []
            if e.get("since_session") is not None:
                sess.append(f"s{e['since_session']}+")
            if e.get("until_session") is not None:
                sess.append(f"closed s{e['until_session']}")
            if e.get("superseded_by"):
                sess.append(f"superseded by {e['superseded_by']}")
            sess_str = " (" + ", ".join(sess) + ")" if sess else ""
            note = f"  — {e['note']}" if e.get("note") else ""
            print(f"  {f_name} --[{e['type']}]--> {t_name}{sess_str}{note}")


# -------- argparse --------

_EXTRACTION_SYSTEM = """You extract relationship facts from D&D campaign session text.

Output ONLY a JSON array (no prose, no preamble). Each element is an edge object:
{
  "from":       "<exact entity name>",
  "to":         "<exact entity name>",
  "type":       "<relationship verb>",
  "since_session": <int>,
  "source": { "file": "<filename>", "session": <int>, "anchor": "<verbatim phrase from text>" },
  "note":       "<optional 1-line clarification>"
}

Rules:
- Use exact entity names as they appear in the text (e.g. "Captain Voss", "Northwall Compact", "The Wharf").
- One edge per discrete fact. Don't compound.
- Capture relational SHIFTS or STRUCTURAL relationships only — not generic ambient action ("Theo walked into the room").
- The `anchor` MUST be a verbatim phrase (or near-verbatim) copied from the source text — short enough to grep for, long enough to be unique.
- Skip self-edges, hypotheticals, hearsay-only claims, DM-meta commentary marked "(DM-side)" or "DM Calibration".
- Common edge types: met, knows, fears, owes, opposes, allied_with, member_of, lives_in, based_at, holds, controls, watches, hunts, referred, introduced_to, committed_to, flagged_offlimits, has_history_with, originates_from, advances_thread, blocks_thread.
- Capture both endpoints of an introduction chain (e.g. "X sent Y to Z" → edge `X → referred_to_Z → Y` AND edge `Y → met → Z`).
- Output [] if no relationships found in the input.
"""


def _build_extraction_prompt(sources: list) -> str:
    parts = []
    parts.append("Extract relationship edges from the following session-log content. Output a single JSON array of edge objects per the system prompt schema.\n")
    for fname, text in sources:
        parts.append(f"=== FILE: {fname} ===\n{text}\n")
    return "\n".join(parts)


def _parse_extraction_output(raw: str) -> list:
    """Parse Haiku output. Tolerates fenced code blocks and stray prose."""
    s = raw.strip()
    # Strip markdown fences if present
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    # Find first '[' and matching ']'
    start = s.find("[")
    end = s.rfind("]")
    if start < 0 or end < 0 or end <= start:
        raise ValueError("no JSON array found in output")
    return json.loads(s[start:end + 1])


def _existing_edge_match(data: dict, frm_id: str, to_id: str, etype: str) -> bool:
    """True if an active edge with same from/to/type already exists."""
    for e in data.get("edges", []):
        if e["from"] == frm_id and e["to"] == to_id and e["type"] == etype and e.get("until_session") is None:
            return True
    return False


_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def _apply_proposal(data: dict, p: dict, no_auto_nodes: bool = False) -> "tuple[bool, int, str]":
    """Apply a single edge proposal to the in-memory graph `data`.

    Resolves or auto-creates the from/to nodes, dedupes against existing edges,
    and appends a new edge in campaign_graph.py's exact edge schema. Idempotent:
    a proposal whose edge already exists is a no-op.

    Returns (applied, nodes_created, message). `applied` is False when the edge
    was skipped (already present, or a node was missing under --no-auto-nodes).
    """
    nodes_created = 0

    def resolve_or_create(name: str, is_category: bool = False) -> str:
        nonlocal nodes_created
        existing_id = _resolve_node(data, name)
        if existing_id:
            return existing_id
        if is_category:
            new_id = f"cat_{_slug(name)}"
            data.setdefault("nodes", []).append({
                "id": new_id, "type": "category", "name": name,
                "tags": [], "summary": "",
                "category_node": True,
                "_auto_created_from_extract": True,
            })
            nodes_created += 1
            return new_id
        if no_auto_nodes:
            raise ValueError(f"node not found and --no-auto-nodes set: {name!r}")
        new_id = f"npc_{_slug(name)}"
        data.setdefault("nodes", []).append({
            "id": new_id, "type": "npc", "name": name, "tags": [], "summary": "",
            "_auto_created_from_extract": True,
        })
        nodes_created += 1
        return new_id

    frm_name = p.get("from", "")
    to_name = p.get("to", "")
    etype = p.get("type", "")
    try:
        frm_id = resolve_or_create(frm_name, is_category=bool(p.get("category_from")))
        to_id = resolve_or_create(to_name, is_category=bool(p.get("category_to")))
    except ValueError as e:
        return False, nodes_created, str(e)

    if _existing_edge_match(data, frm_id, to_id, etype):
        return False, nodes_created, "already in graph"

    edge = {
        "id": _next_edge_id(data["edges"]),
        "from": frm_id,
        "to": to_id,
        "type": etype,
        "since_session": p.get("since_session"),
        "until_session": None,
        "note": p.get("note") or "",
    }
    if p.get("source"):
        edge["source"] = p["source"]
    data["edges"].append(edge)
    return True, nodes_created, f"{edge['id']}  {frm_id} --[{etype}]--> {to_id}"


def cmd_extract(args) -> int:
    import subprocess
    campaign_dir = find_campaign(args.campaign)

    # Deterministic mode — pattern-match against verb_table_seed.yaml. No LLM.
    if getattr(args, "deterministic", False):
        from graph_extract_deterministic import extract_proposals as _det_extract
        proposals = _det_extract(
            campaign_dir,
            last_session_only=getattr(args, "last_session_only", False),
        )

        # One-shot auto-apply: filter to proposals at/above --min-confidence and
        # write them straight into graph.json, deduped + idempotent.
        if getattr(args, "apply", False):
            min_rank = _CONFIDENCE_RANK.get(getattr(args, "min_confidence", "high"), 2)
            data = _load(args.campaign)
            applied_edges = applied_nodes = skipped = below = 0
            for p in proposals:
                rank = _CONFIDENCE_RANK.get(p.get("confidence", "medium"), 1)
                if rank < min_rank:
                    below += 1
                    continue
                ok, made, msg = _apply_proposal(data, p,
                                                no_auto_nodes=getattr(args, "no_auto_nodes", False))
                applied_nodes += made
                if ok:
                    applied_edges += 1
                    print(f"  applied {msg}")
                else:
                    skipped += 1
            _save(args.campaign, data)
            print(f"# done: +{applied_nodes} nodes, +{applied_edges} edges, "
                  f"{skipped} already present, {below} below {getattr(args,'min_confidence','high')}-confidence")
            return 0

        out_json = json.dumps(proposals, indent=2, ensure_ascii=False)
        print(f"# Deterministic extraction — {len(proposals)} proposals from "
              f"{campaign_dir.name}", file=sys.stderr)
        if getattr(args, "write", None):
            pathlib.Path(args.write).write_text(out_json)
            print(f"# wrote proposals to {args.write}", file=sys.stderr)
        else:
            print(out_json)
        return 0

    archive = campaign_dir / "session-log-archive.md"
    log = campaign_dir / "session-log.md"

    sources = []
    if archive.exists():
        sources.append((archive.name, archive.read_text()))
    if log.exists():
        sources.append((log.name, log.read_text()))
    if not sources:
        print(f"no session-log files in {campaign_dir}", file=sys.stderr)
        return 1

    prompt = _build_extraction_prompt(sources)
    print(f"# Calling Haiku — input chars: {len(prompt)} from {len(sources)} files", file=sys.stderr)

    result = subprocess.run(
        [
            "claude", "-p",
            "--model", "claude-haiku-4-5-20251001",
            "--system-prompt", _EXTRACTION_SYSTEM,
            prompt,
        ],
        capture_output=True, text=True, timeout=420,
    )
    if result.returncode != 0:
        print(f"claude returned {result.returncode}: {result.stderr}", file=sys.stderr)
        return 1

    try:
        proposals = _parse_extraction_output(result.stdout)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"failed to parse output: {e}", file=sys.stderr)
        print("raw output follows:", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        return 1

    # Dedupe against existing graph
    data = _load(args.campaign)
    deduped: list = []
    skipped = 0
    for p in proposals:
        # Resolve from/to to ids if they exist; otherwise leave as names (apply step will handle creation)
        frm_id = _resolve_node(data, p.get("from", "")) or p.get("from", "")
        to_id = _resolve_node(data, p.get("to", "")) or p.get("to", "")
        if _existing_edge_match(data, frm_id, to_id, p.get("type", "")):
            skipped += 1
            continue
        p["_resolved_from"] = frm_id
        p["_resolved_to"] = to_id
        deduped.append(p)

    print(f"# {len(proposals)} extracted, {skipped} already in graph, {len(deduped)} new")
    print()
    for i, p in enumerate(deduped, 1):
        src = p.get("source", {}) or {}
        anchor = (src.get("anchor") or "")[:90]
        print(f"{i:3}. {p['_resolved_from']} --[{p['type']}]--> {p['_resolved_to']} (s{p.get('since_session','?')}+)")
        if anchor:
            print(f"      src: {src.get('file','?')} s{src.get('session','?')} — \"{anchor}\"")
        if p.get("note"):
            print(f"      note: {p['note']}")
        print()

    if args.write:
        out = pathlib.Path(args.write).expanduser()
        out.write_text(json.dumps(deduped, indent=2))
        print(f"# wrote {len(deduped)} proposals to {out}", file=sys.stderr)

    return 0


def cmd_extract_apply(args) -> int:
    """Apply edge proposals from a JSON file produced by extract --write."""
    proposals_path = pathlib.Path(args.proposals).expanduser()
    if not proposals_path.exists():
        print(f"proposals file not found: {proposals_path}", file=sys.stderr)
        return 1
    proposals = json.loads(proposals_path.read_text())
    pick = None
    if args.pick:
        pick = set(int(x.strip()) for x in args.pick.split(",") if x.strip())

    review = bool(getattr(args, "review", False))
    if review and pick is not None:
        print("error: --review and --pick are mutually exclusive", file=sys.stderr)
        return 2

    data = _load(args.campaign)
    applied_nodes = 0
    applied_edges = 0
    skipped = 0
    review_skipped = 0

    def _review_prompt(idx: int, total: int, p: dict) -> str:
        """Show one proposal and prompt y/n/q. Returns 'y' / 'n' / 'q'."""
        src = p.get("source", {}) or {}
        anchor = (src.get("anchor") or "")[:140]
        conf = p.get("confidence", "?")
        print(f"\n[{idx}/{total}] {p.get('from','?')} --[{p.get('type','?')}]--> {p.get('to','?')}"
              f"  (s{p.get('since_session','?')}+, confidence={conf})")
        if anchor:
            print(f"    src: {src.get('file','?')} s{src.get('session','?')} — \"{anchor}\"")
        if p.get("note"):
            print(f"    note: {p['note']}")
        while True:
            try:
                a = input("    apply? [y]es / [n]o / [q]uit: ").strip().lower()
            except EOFError:
                return "q"
            if a in {"y", "yes", ""}:
                return "y"
            if a in {"n", "no", "s", "skip"}:
                return "n"
            if a in {"q", "quit", "exit"}:
                return "q"
            print("    please enter y / n / q")

    quit_review = False
    for i, p in enumerate(proposals, 1):
        if quit_review:
            review_skipped += 1
            continue
        if pick is not None and i not in pick:
            continue
        if review:
            decision = _review_prompt(i, len(proposals), p)
            if decision == "q":
                quit_review = True
                review_skipped += 1
                continue
            if decision == "n":
                review_skipped += 1
                continue
        ok, made, msg = _apply_proposal(data, p, no_auto_nodes=args.no_auto_nodes)
        applied_nodes += made
        if ok:
            applied_edges += 1
            print(f"  applied {msg}")
        else:
            if msg != "already in graph":
                print(f"  skip {i}: {msg}", file=sys.stderr)
            skipped += 1

    _save(args.campaign, data)
    msg = f"# done: +{applied_nodes} nodes, +{applied_edges} edges, {skipped} skipped"
    if review_skipped:
        msg += f", {review_skipped} declined in review"
    print(msg)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="campaign_graph")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_camp(sp):
        sp.add_argument("--campaign", required=True)

    sp = sub.add_parser("add-node")
    add_camp(sp)
    sp.add_argument("--type", required=True,
                    help="node type (npc, faction, place, item, thread, ...)")
    sp.add_argument("--name", required=True)
    sp.add_argument("--id", help="explicit id (default: <type>_<name-slug>)")
    sp.add_argument("--tags", help="comma-separated")
    sp.add_argument("--summary", help="one-line summary")
    sp.set_defaults(func=cmd_add_node)

    sp = sub.add_parser("add-edge")
    add_camp(sp)
    sp.add_argument("--from", dest="from_id", required=True)
    sp.add_argument("--to", dest="to_id", required=True)
    sp.add_argument("--type", required=True,
                    help="edge type (loyal_to, opposes, lives_in, ...)")
    sp.add_argument("--since", dest="since", type=int, default=None,
                    help="session number when edge became active")
    sp.add_argument("--until", dest="until", type=int, default=None,
                    help="session number when edge ended (rare on add)")
    sp.add_argument("--note")
    sp.set_defaults(func=cmd_add_edge)

    sp = sub.add_parser("close-edge")
    add_camp(sp)
    sp.add_argument("--id", required=True, help="edge id to close")
    sp.add_argument("--at-session", dest="at_session", type=int, required=True)
    sp.add_argument("--anchor",
        help="verbatim phrase from canon that justifies the closure (recorded as closed_anchor)")
    sp.set_defaults(func=cmd_close_edge)

    sp = sub.add_parser("supersede-edge",
        help="mark an edge as superseded (hard retcon) — preserves audit trail "
             "but excludes from active queries")
    add_camp(sp)
    sp.add_argument("--id", required=True, help="edge id to mark wrong")
    sp.add_argument("--by", help="optional id of the corrected edge that replaces it")
    sp.add_argument("--reason", help="one-line explanation of the retcon")
    sp.set_defaults(func=cmd_supersede_edge)

    sp = sub.add_parser("list")
    add_camp(sp)
    sp.add_argument("--type", help="filter by node type")
    sp.add_argument("--at-session", dest="at_session", type=int, default=None)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show")
    add_camp(sp)
    sp.add_argument("--id", required=True)
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("subgraph")
    add_camp(sp)
    sp.add_argument("--seed", action="append", required=True,
                    help="repeat for multiple seeds")
    sp.add_argument("--hops", type=int, default=2)
    sp.add_argument("--at-session", dest="at_session", type=int, default=None)
    sp.set_defaults(func=cmd_subgraph)

    sp = sub.add_parser("scene-context")
    add_camp(sp)
    sp.add_argument("--place")
    sp.add_argument("--present", help="comma-separated node ids in scene")
    sp.add_argument("--threads", help="comma-separated thread node ids")
    sp.add_argument("--hops", type=int, default=2)
    sp.add_argument("--at-session", dest="at_session", type=int, default=None)
    sp.set_defaults(func=cmd_scene_context)

    sp = sub.add_parser("extract",
        help="Pattern-based or Haiku-backed extraction over session-log to propose edges with source anchors")
    add_camp(sp)
    sp.add_argument("--write", metavar="FILE",
        help="also write proposals as JSON for later --apply review")
    sp.add_argument("--deterministic", action="store_true",
        help="use the verb-table seed to pattern-match (no LLM, ~50%% recall, ~95%% precision)")
    sp.add_argument("--last-session-only", action="store_true",
        help="only scan the last session block of session-log.md (skip the archive)")
    sp.add_argument("--apply", action="store_true",
        help="(deterministic mode) auto-apply proposals at/above --min-confidence "
             "straight into graph.json, deduped + idempotent — no review file")
    sp.add_argument("--min-confidence", choices=["low", "medium", "high"],
        default="high",
        help="(with --apply) lowest confidence tier to auto-apply (default: high)")
    sp.add_argument("--no-auto-nodes", action="store_true",
        help="(with --apply) skip edges referencing unknown nodes instead of "
             "auto-creating npc placeholders")
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("extract-apply",
        help="Apply edge proposals from a JSON file produced by extract --write")
    add_camp(sp)
    sp.add_argument("--proposals", required=True, metavar="FILE",
        help="proposals JSON file path")
    sp.add_argument("--pick", metavar="N1,N2,...",
        help="apply only the listed proposal numbers (1-indexed); default: apply all")
    sp.add_argument("--review", action="store_true",
        help="walk proposals one at a time with y/n/q prompts; mutually exclusive with --pick")
    sp.add_argument("--no-auto-nodes", action="store_true",
        help="error on edges referencing unknown nodes instead of auto-creating npc placeholders")
    sp.set_defaults(func=cmd_extract_apply)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
