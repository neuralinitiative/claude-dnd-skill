#!/usr/bin/env python3
"""
external_corpus_extract.py — Batched verb extraction over collected corpus.

Reads .md posts from data/external-corpus/, batches them
~10 per Haiku call (to amortize the ~5-min latency), and aggregates verb-frequency
counts across the corpus. Output is verb stats only — does NOT write to graph.json.

Sandbox tool. Used to seed verb_table_seed.yaml inclusion/exclusion lists with
verbs that appear consistently across diverse public D&D narrative.

Usage:
    python3 external_corpus_extract.py \\
        --corpus-dir data/external-corpus \\
        --top-n 50 \\
        --batch-size 10 \\
        --out /tmp/external-verb-stats.json
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
from collections import Counter

EXTRACTION_SYSTEM = """You extract relationship verbs from D&D narrative text for verb-frequency research.

For each post, identify all sentences that describe a relationship or action between two named entities (people, factions, places, characters, NPCs). For each such sentence, output the VERB (lemmatized to base form) along with whether the relationship is concrete (both entities are clearly named) or abstract (one or both entities are pronouns/concepts).

Output STRICT JSON only:
{
  "extractions": [
    {
      "post_id": "<from post header>",
      "verb": "<base form lemma, e.g. 'killed', 'led', 'introduced'>",
      "concrete": true,
      "anchor": "<verbatim phrase ≤80 chars>"
    },
    ...
  ]
}

Rules:
- Lemmatize: "killed/kills/killing" → "killed" (use past-simple form)
- Skip pronoun-mediated sentences ("she did X to him") unless context resolves them
- Skip mechanical content (dice, HP, AC, rolls)
- Skip rules questions / meta commentary
- Skip self-edges (X did X to themselves)
- Multiple posts may be in one input — keep post_id distinct per extraction
- Aim for breadth: capture every verb that connects two entities, not just the high-confidence ones
- Output [] if nothing extractable

Input format: each post separated by '<<<POST: <id>>>>' marker followed by the post body.
"""


def call_haiku(prompt: str, system: str, timeout_sec: int = 600) -> str:
    """Invoke claude CLI with Haiku model."""
    result = subprocess.run(
        [
            "claude", "-p",
            "--model", "claude-haiku-4-5-20251001",
            "--system-prompt", system,
            prompt,
        ],
        capture_output=True, encoding="utf-8", text=True, timeout=timeout_sec,
    )
    if result.returncode != 0:
        sys.stderr.write(f"[haiku] returned {result.returncode}: {result.stderr[:300]}\n")
        return ""
    return result.stdout


def parse_extractions(raw: str) -> list:
    """Tolerant JSON parser. Returns list of extraction dicts."""
    s = raw.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    # Find a JSON object containing "extractions"
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0:
        return []
    try:
        obj = json.loads(s[start:end + 1])
        return obj.get("extractions", [])
    except json.JSONDecodeError as e:
        sys.stderr.write(f"[parse] {e}\n")
        return []


def load_posts(corpus_dir: pathlib.Path, top_n: int) -> list:
    """Load all .md posts, sort by word count desc, take top_n."""
    posts = []
    for md in corpus_dir.rglob("*.md"):
        if md.name == "_summary.json":
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        words = len(text.split())
        # Extract post_id from header
        m = re.search(r"\*\*Post ID:\*\*\s+(\S+)", text)
        post_id = m.group(1) if m else md.stem
        posts.append({
            "path": md,
            "post_id": post_id,
            "word_count": words,
            "text": text,
        })
    posts.sort(key=lambda p: p["word_count"], reverse=True)
    return posts[:top_n]


def build_batch_prompt(batch: list) -> str:
    parts = ["Extract relationship verbs from the following posts. Output a single JSON object as specified.\n"]
    for p in batch:
        parts.append(f"<<<POST: {p['post_id']}>>>")
        # Cap each post at 4000 chars to keep batch size manageable
        body = p["text"][:4000]
        parts.append(body)
        parts.append("")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-dir", required=True, type=pathlib.Path)
    ap.add_argument("--top-n", type=int, default=50)
    ap.add_argument("--batch-size", type=int, default=10)
    ap.add_argument("--out", required=True, type=pathlib.Path)
    args = ap.parse_args()

    corpus_dir = args.corpus_dir.expanduser()
    if not corpus_dir.exists():
        print(f"missing corpus dir: {corpus_dir}", file=sys.stderr)
        return 1

    posts = load_posts(corpus_dir, args.top_n)
    print(f"[load] {len(posts)} posts (top {args.top_n} by word count)")

    all_extractions = []
    n_batches = (len(posts) + args.batch_size - 1) // args.batch_size
    for i in range(0, len(posts), args.batch_size):
        batch = posts[i:i + args.batch_size]
        batch_num = i // args.batch_size + 1
        total_chars = sum(len(p["text"]) for p in batch)
        print(f"[batch {batch_num}/{n_batches}] {len(batch)} posts, {total_chars} chars input")
        prompt = build_batch_prompt(batch)
        t0 = time.time()
        raw = call_haiku(prompt, EXTRACTION_SYSTEM)
        elapsed = time.time() - t0
        if not raw:
            print(f"  [batch {batch_num}] empty response, skipping", file=sys.stderr)
            continue
        extractions = parse_extractions(raw)
        print(f"  [batch {batch_num}] {len(extractions)} extractions in {elapsed:.0f}s")
        all_extractions.extend(extractions)

    # Aggregate verb stats
    verb_counts = Counter()
    verb_concrete = Counter()  # how many were concrete (both entities resolved)
    verb_abstract = Counter()
    posts_per_verb: dict = {}
    for e in all_extractions:
        verb = (e.get("verb") or "").strip().lower().replace(" ", "_")
        if not verb:
            continue
        verb_counts[verb] += 1
        if e.get("concrete"):
            verb_concrete[verb] += 1
        else:
            verb_abstract[verb] += 1
        posts_per_verb.setdefault(verb, set()).add(e.get("post_id", "?"))

    # Output: sorted verbs with stats
    output = {
        "summary": {
            "n_posts_processed": len(posts),
            "n_extractions": len(all_extractions),
            "n_unique_verbs": len(verb_counts),
        },
        "verb_stats": [
            {
                "verb": v,
                "count": verb_counts[v],
                "concrete": verb_concrete[v],
                "abstract": verb_abstract[v],
                "concrete_rate": verb_concrete[v] / verb_counts[v] if verb_counts[v] else 0,
                "n_posts": len(posts_per_verb[v]),
            }
            for v in sorted(verb_counts, key=lambda k: verb_counts[k], reverse=True)
        ],
        "extractions": all_extractions,
    }

    out_path = args.out.expanduser()
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"[done] {out_path}")

    # Summary to stdout
    print(f"\n--- top 30 verbs by count (concrete% | n_posts) ---")
    for vs in output["verb_stats"][:30]:
        print(f"  {vs['count']:>3}  {vs['verb']:<28} ({vs['concrete_rate']*100:>3.0f}% concrete; {vs['n_posts']} posts)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
