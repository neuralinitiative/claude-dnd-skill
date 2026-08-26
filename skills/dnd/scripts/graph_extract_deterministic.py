"""
graph_extract_deterministic.py — pattern-based extractor for campaign_graph.

LLM-free alternative to the Haiku-backed `extract` subcommand. Uses the verb
table seed (data/graph/verb_table_seed.yaml) to pattern-match sentences in
session-log files and propose typed edges with verbatim source anchors.

The output format matches the Haiku extractor exactly so that `extract-apply`
can consume proposals from either source.

Trade vs. Phase 1 (Haiku):
  - Recall ~50% (clean SVO + SVO-with-prep only; pronouns and complex grammar skipped)
  - Precision ~95% (vs ~85% Haiku, before paraphrase tolerance)
  - Cost: zero LLM calls
  - Portability: no Claude API dependency — usable in open-tabletop-gm too
"""
import json
import pathlib
import re
import sys
from typing import Optional

try:
    import yaml
except ImportError:
    print("error: PyYAML required for deterministic extraction. Install: pip3 install pyyaml",
          file=sys.stderr)
    sys.exit(2)


_SKILL_ROOT = pathlib.Path(__file__).resolve().parent.parent
# Public repo layout (claude-dnd-skill repo) puts the seed under data/graph/.
# The skill keeps it at data/. Look in both.
_VERB_TABLE_CANDIDATES = (
    _SKILL_ROOT / "data" / "graph" / "verb_table_seed.yaml",
    _SKILL_ROOT / "data" / "verb_table_seed.yaml",
)


# ── verb table ──────────────────────────────────────────────────────────────


def load_verb_table(path: Optional[pathlib.Path] = None) -> dict:
    if path is not None:
        candidates = (path,)
    else:
        candidates = _VERB_TABLE_CANDIDATES
    for p in candidates:
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return yaml.safe_load(f)
    tried = "\n  ".join(str(p) for p in candidates)
    raise FileNotFoundError(f"verb table not found. tried:\n  {tried}")


# ── entity recognition ─────────────────────────────────────────────────────


_NPC_TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|")
_NPC_FULL_HEADING_RE = re.compile(r"^###\s+(.+?)\s*$")
_FACTION_HEADING_RE = re.compile(r"^###\s+(?:The\s+)?([A-Z][\w\s'-]+?)\s*$")
_PLACE_HEADING_RE = re.compile(r"^###?\s+([A-Z][\w\s'-]+?)\s*$")


def _is_likely_name(s: str) -> bool:
    """Heuristic: entity names start with a capital and aren't pure header text."""
    if not s or not s[0].isupper():
        return False
    if s.lower() in {"name", "npc", "faction", "place", "n/a", "none", "tbd", "---"}:
        return False
    if any(c in s for c in "|*_<>"):
        return False
    if len(s) > 60:  # too long, probably free-text
        return False
    return True


def build_entity_set(campaign_dir: pathlib.Path) -> set:
    """Extract entity names from canonical sources for pattern matching.

    Reads (in order, deduping by name):
      - npcs.md table (first column, skipping header rows)
      - npcs-full.md headings (### NPC Name)
      - world.md headings (### Faction or Place)
    """
    entities: set = set()

    # npcs.md table
    npcs = campaign_dir / "npcs.md"
    if npcs.exists():
        for line in npcs.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _NPC_TABLE_ROW_RE.match(line)
            if m and _is_likely_name(m.group(1)):
                entities.add(m.group(1).strip())

    # npcs-full.md headings
    full = campaign_dir / "npcs-full.md"
    if full.exists():
        for line in full.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _NPC_FULL_HEADING_RE.match(line)
            if m and _is_likely_name(m.group(1)):
                entities.add(m.group(1).strip())

    # world.md — H3 headings for factions and places
    world = campaign_dir / "world.md"
    if world.exists():
        in_factions = False
        in_places = False
        for line in world.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("## "):
                in_factions = "faction" in line.lower()
                in_places = ("place" in line.lower() or "location" in line.lower()
                             or "geography" in line.lower())
            elif (in_factions or in_places) and line.startswith("### "):
                m = _NPC_FULL_HEADING_RE.match(line)
                if m and _is_likely_name(m.group(1)):
                    entities.add(m.group(1).strip())

    return entities


_STOP_WORD_FIRSTS = {
    # Articles / prepositions that lead faction or place names ("The Council",
    # "Order of the Black Lily"). NEVER add these as standalone aliases —
    # they'd match every "the"/"a"/"of" in any sentence under case-insensitive
    # matching and turn the extractor into a noise machine.
    "the", "a", "an", "of", "in", "at", "for", "to", "by", "on", "and", "or",
    "but", "from", "with", "into", "onto", "upon", "about", "over", "under",
}


def build_alias_index(canonical: set) -> dict:
    """Build alias-to-canonical lookup so the extractor can resolve short forms.

    Real campaigns mix name forms — npcs.md may list "Mayor Aldric Brandt"
    but the session log uses "Aldric Brandt", "Aldric", or "Brandt" inter-
    changeably. We generate aliases from every individual word + every
    contiguous multi-word subsequence of each canonical, then keep the alias
    only if exactly one canonical claims it (unambiguous) and it's not a
    stop word.

    Returns a dict mapping every alias → canonical. Canonical names are also
    entries (self-mapping).
    """
    aliases: dict = {name: name for name in canonical}

    # Build candidate map: alias-form → set of canonicals that produce it
    candidate_owners: dict = {}
    for name in canonical:
        parts = name.split()
        if len(parts) <= 1:
            continue
        # Single words
        for word in parts:
            if word.lower() in _STOP_WORD_FIRSTS:
                continue
            candidate_owners.setdefault(word, set()).add(name)
        # Contiguous multi-word subsequences (length 2..n-1; full length is
        # already canonical so skip it)
        n = len(parts)
        for length in range(2, n):
            for start in range(0, n - length + 1):
                sub = " ".join(parts[start:start + length])
                # Don't strip stop-word leading prefixes here — "Aldric Brandt"
                # is fine, "The Council" suffix handling is moot since canonical
                # itself catches it.
                first_word_lower = parts[start].lower()
                if first_word_lower in _STOP_WORD_FIRSTS:
                    continue
                candidate_owners.setdefault(sub, set()).add(name)

    # Promote unambiguous candidates to aliases
    for alias, owners in candidate_owners.items():
        if len(owners) != 1:
            continue
        # Don't shadow an existing canonical
        if alias in canonical:
            continue
        owner = next(iter(owners))
        aliases[alias] = owner

    return aliases


# ── sentence + pattern handling ─────────────────────────────────────────────


def split_sentences(text: str) -> list:
    """Sentence-tokenize. Conservative — splits on . ! ? followed by whitespace."""
    sents = re.split(r"(?<=[.!?])\s+", text)
    out = []
    for s in sents:
        s = s.strip()
        if s and len(s) >= 8:  # skip stubs
            out.append(s)
    return out


def _build_entity_alternation(entities: set) -> str:
    """Regex alternation of entity names, longest first to prefer 'Sorn Thrace' over 'Sorn'."""
    ordered = sorted(entities, key=len, reverse=True)
    return "|".join(re.escape(e) for e in ordered if e)


def build_pattern_regex(template: str, entity_alt: str,
                        category_target: Optional[str] = None) -> Optional[re.Pattern]:
    """Convert a verb-table pattern template (e.g. 'X sent Y to Z') to a regex.

    Placeholders X, Y, Z become named capture groups matching any known entity.
    All other whitespace becomes flexible (\\s+); other tokens stay literal.

    If `category_target` is one of {'X','Y','Z'}, that slot matches a categorical
    noun phrase ("a ghost", "the gods") instead of a named entity. The capture
    group still has the name X/Y/Z but the matched text is the bare noun
    ("ghost", "the gods"). Used for state-verbs with `category_object_ok: true`.

    Returns None on regex compile error.
    """
    if not entity_alt:
        return None
    # Recognize X/Y/Z as entity slots and V as a verb-phrase wildcard.
    # V is used in future-tense templates like "X plans to V Y" where the
    # verb between the modal phrase and the object varies across sentences
    # ("plans to file", "plans to meet", "plans to attack the city").
    parts = re.split(r"\b([XYZV])\b", template)
    rebuilt = []
    seen = set()
    # Categorical target: "a/an/the/some" + 1-2 lowercase nouns. Capture only
    # the noun ("ghost" not "a ghost") so the category node name is clean.
    for tok in parts:
        if tok in {"X", "Y", "Z"}:
            if tok in seen:
                rebuilt.append(rf"(?P={tok})")
            elif tok == category_target:
                rebuilt.append(
                    rf"(?:a|an|the|some)\s+(?P<{tok}>[a-z][a-z\-]*(?:\s+[a-z][a-z\-]*)?)"
                )
                seen.add(tok)
            else:
                rebuilt.append(rf"(?P<{tok}>{entity_alt})")
                seen.add(tok)
        elif tok == "V":
            # Wildcard for variable verb phrases — match 1 to 4 short LOWERCASE
            # words. The (?-i:...) inline flag disables IGNORECASE for this
            # group only, so V can't accidentally consume a capitalized
            # entity prefix (e.g. eating "Aldric" before reaching "Brandt").
            rebuilt.append(r"(?-i:(?:[a-z\'\-]+\s+){0,3}[a-z\'\-]+)")
        else:
            tok_esc = re.escape(tok)
            tok_esc = re.sub(r"(?:\\\s)+", r"\\s+", tok_esc)
            rebuilt.append(tok_esc)
    pattern = "".join(rebuilt)
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None


# ── source-context helpers ──────────────────────────────────────────────────


def session_for_offset(text: str, offset: int) -> Optional[int]:
    """Find the most recent '## Session N' / 'Session N' header before `offset`."""
    head = text[:offset]
    matches = list(re.finditer(r"^#{1,3}\s*Session\s+(\d+)", head, re.MULTILINE | re.IGNORECASE))
    if matches:
        try:
            return int(matches[-1].group(1))
        except ValueError:
            return None
    return None


# ── extraction ──────────────────────────────────────────────────────────────


def _coerce_emits(emits) -> list:
    """Pattern emits can be a single dict or a list of dicts; normalize to list."""
    if emits is None:
        return []
    if isinstance(emits, dict):
        return [emits]
    return list(emits)


def _resolve_slot(slot, match: "re.Match") -> Optional[str]:
    """Resolve an emit slot ('X', 'Y', 'Z') to the captured entity name."""
    if slot in ("X", "Y", "Z"):
        try:
            return match.group(slot)
        except (IndexError, KeyError):
            return None
    return slot  # literal entity name (uncommon)


def extract_proposals(campaign_dir: pathlib.Path,
                      last_session_only: bool = False,
                      verb_table: Optional[dict] = None) -> list:
    """Run deterministic extraction over the campaign's session logs.

    Returns a list of edge proposal dicts in the same shape the Haiku extractor produces:
      {"from": str, "to": str, "type": str, "since_session": int|None,
       "source": {"file": str, "session": int|None, "anchor": str},
       "confidence": "high"|"medium"|"low",
       "note": str (optional)}
    """
    verb_table = verb_table or load_verb_table()
    entities = build_entity_set(campaign_dir)
    if not entities:
        return []

    aliases = build_alias_index(entities)
    # Match against any known form (canonical OR alias); canonicalize on emit.
    entity_alt = _build_entity_alternation(set(aliases.keys()))

    sources: list = []
    archive = campaign_dir / "session-log-archive.md"
    log = campaign_dir / "session-log.md"
    if not last_session_only and archive.exists():
        sources.append((archive.name, archive.read_text(encoding="utf-8", errors="replace")))
    if log.exists():
        if last_session_only:
            # Trim to the last "## Session N" block only
            text = log.read_text(encoding="utf-8", errors="replace")
            heads = list(re.finditer(r"^##\s*Session\s+\d+", text, re.MULTILINE | re.IGNORECASE))
            if heads:
                text = text[heads[-1].start():]
            sources.append((log.name, text))
        else:
            sources.append((log.name, log.read_text(encoding="utf-8", errors="replace")))

    # Process inclusion (high-confidence auto-emit) AND borderline (lower-
    # confidence; the extractor still emits them but the confidence field
    # signals they need human review). Iterating both gives much better recall.
    entries = list(verb_table.get("inclusion") or []) + list(verb_table.get("borderline") or [])

    proposals: list = []
    seen: set = set()  # dedupe by (from, to, type, anchor) tuple

    for verb_entry in entries:
        edge_type = verb_entry.get("edge_type")
        confidence = verb_entry.get("confidence", "medium")
        symmetric = bool(verb_entry.get("symmetric"))
        cat_ok = bool(verb_entry.get("category_object_ok"))
        for pattern_dict in verb_entry.get("patterns", []) or []:
            template = pattern_dict.get("template")
            if not template:
                continue
            emits = _coerce_emits(pattern_dict.get("emits"))
            if not emits:
                continue
            # Build the pattern variants to try: (regex, category_slot_or_None).
            # When category_object_ok is true, also try a category-target variant
            # for the verb's grammatical object — the LAST X/Y/Z placeholder in
            # the template (e.g. Y in "X is possessed by Y", Y in "X worships Y").
            variants = [(build_pattern_regex(template, entity_alt), None)]
            if cat_ok:
                slot_order = re.findall(r"\b([XYZ])\b", template)
                cat_slot = slot_order[-1] if slot_order else None
                if cat_slot in {"X", "Y", "Z"}:
                    cat_re = build_pattern_regex(template, entity_alt,
                                                 category_target=cat_slot)
                    if cat_re is not None:
                        variants.append((cat_re, cat_slot))
            for pat_re, cat_slot in variants:
                if pat_re is None:
                    continue
                for src_file, src_text in sources:
                    for match in pat_re.finditer(src_text):
                        sent_start = src_text.rfind(".", 0, match.start()) + 1
                        sent_end = src_text.find(".", match.end())
                        sent_end = len(src_text) if sent_end == -1 else sent_end + 1
                        anchor = src_text[sent_start:sent_end].strip()
                        if len(anchor) > 240:
                            anchor = src_text[match.start():match.end()].strip()
                        sess = session_for_offset(src_text, match.start())
                        for emit in emits:
                            frm = _resolve_slot(emit.get("from"), match)
                            to = _resolve_slot(emit.get("to"), match)
                            etype = emit.get("type") or edge_type
                            if not frm or not to:
                                continue
                            # Canonicalize alias matches (e.g. "Aldric" → "Aldric Brandt").
                            # Skip canonicalization for the categorical slot — its
                            # captured value is a category noun, not an entity.
                            if emit.get("from") != cat_slot:
                                frm = aliases.get(frm, frm)
                            if emit.get("to") != cat_slot:
                                to = aliases.get(to, to)
                            if frm == to:
                                continue
                            if symmetric and frm > to:
                                frm, to = to, frm
                            key = (frm, to, etype, anchor)
                            if key in seen:
                                continue
                            seen.add(key)
                            proposal = {
                                "from": frm,
                                "to": to,
                                "type": etype,
                                "since_session": sess,
                                "source": {"file": src_file, "session": sess, "anchor": anchor},
                                "confidence": confidence,
                            }
                            if cat_slot is not None:
                                # Mark which side of the edge is a category target
                                if emit.get("from") == cat_slot:
                                    proposal["category_from"] = True
                                if emit.get("to") == cat_slot:
                                    proposal["category_to"] = True
                                # Categorical proposals start at lower confidence
                                proposal["confidence"] = "low" if confidence == "high" else confidence
                            if emit.get("note"):
                                proposal["note"] = emit["note"]
                            proposals.append(proposal)

    return proposals


# ── CLI shim for standalone testing ─────────────────────────────────────────


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 2)[1])
    ap.add_argument("--campaign-dir", required=True, type=pathlib.Path,
                    help="path to the campaign directory")
    ap.add_argument("--last-session-only", action="store_true")
    ap.add_argument("--write", metavar="FILE", help="write proposals JSON to FILE")
    args = ap.parse_args()

    proposals = extract_proposals(args.campaign_dir, args.last_session_only)
    out = json.dumps(proposals, indent=2, ensure_ascii=False)
    if args.write:
        pathlib.Path(args.write).write_text(out, encoding="utf-8")
        print(f"# wrote {len(proposals)} proposals to {args.write}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
