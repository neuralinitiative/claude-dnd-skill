#!/usr/bin/env python3
"""
external_corpus_collect.py — Reddit-based corpus collector for verb-table research.

Sandbox tool. Fetches public posts from D&D-adjacent subreddits via Reddit's
JSON API (no auth), normalizes them, and saves them as anonymized .md files
for downstream verb-extraction analysis.

Design constraints:
- Read-only. Never posts, votes, or scrapes user comments.
- Rate-limited: minimum 2.0 sec between requests (well under Reddit's 60 req/min).
- Anonymizes usernames at collect time (u/<name> → [USER]).
- Local cache only; never republished.
- Honors robots.txt; identifies via real User-Agent.

Usage:
    python3 external_corpus_collect.py \\
        --subreddit DMAcademy \\
        --flair "Need Advice" \\
        --time year \\
        --sort top \\
        --limit 50 \\
        --out-dir data/external-corpus/dmacademy

    python3 external_corpus_collect.py \\
        --subreddit CritCrab \\
        --time all \\
        --limit 50 \\
        --out-dir data/external-corpus/critcrab

Output: one .md per accepted post + summary log.
"""

import argparse
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Optional

USER_AGENT = "CampaignGraphResearch/0.1 (research; verb-extraction; contact=local)"
RATE_LIMIT_SEC = 2.0          # minimum delay between requests
TIMEOUT = 30
MIN_WORDS = 250              # lowered from 500 — DMAcademy posts run short
MIN_CAPITALIZED_TOKENS = 3    # rough named-entity proxy

# Strip these patterns from raw post body
NOISE_PATTERNS = [
    re.compile(r"\b\d*d\d+\s*[\+\-]?\s*\d*\s*(?:=\s*\d+)?\b", re.I),  # dice rolls
    re.compile(r"\bAC\s+\d+\b", re.I),
    re.compile(r"\bHP\s*\d+\s*/\s*\d+\b", re.I),
    re.compile(r"\bDC\s+\d+\b", re.I),
    re.compile(r"\bspell\s*slot\s*level?\s*\d+\b", re.I),
    re.compile(r"\b\d+\s*xp\b", re.I),
    re.compile(r"\b\(\d+\s*hp\)\b", re.I),
    re.compile(r"\bedit\s*\d?:.{0,200}", re.I),  # edit notes
    re.compile(r"\bTL;DR.{0,500}", re.I),         # TL;DR sections (often summaries)
    re.compile(r"\bUPDATE\s*\d?:.{0,200}", re.I),
]

# Reject posts whose title or body matches these (rules questions, not narrative)
RULES_QUESTION_PATTERNS = [
    re.compile(r"^(can|how|what|does|is|are|do)\s.+\?", re.I),
    re.compile(r"\b(RAW|rules as written)\b", re.I),
]

USERNAME_PATTERN = re.compile(r"\b/?u/[A-Za-z0-9_-]+\b")


def _request(url: str) -> dict:
    """GET a Reddit JSON URL with rate-limiting and User-Agent."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = resp.read()
            return json.loads(data)
    except Exception as e:
        sys.stderr.write(f"[fetch] {url} → {e}\n")
        return {}


def fetch_listing(subreddit: str, flair: Optional[str], sort: str, time_filter: str,
                  limit: int, query: Optional[str] = None) -> list:
    """Fetch listing of post stubs from subreddit. Returns list of post dicts.

    If `query` is given, uses Reddit's search endpoint with that phrase; combined
    with flair via AND if both are present. If neither, listing is by sort/time.
    """
    posts: list = []
    after = None
    fetched = 0
    page = 0
    while fetched < limit and page < 10:  # max 10 pages safety
        page += 1
        if query or flair:
            parts = []
            if query:
                parts.append(query)
            if flair:
                parts.append(f'flair:"{flair}"')
            q = " AND ".join(parts)
            sr = subreddit.lower()
            if sr == "all":
                # cross-reddit search — drop restrict_sr
                url = (f"https://www.reddit.com/search.json"
                       f"?q={urllib.parse.quote(q)}"
                       f"&sort={sort}&t={time_filter}&limit=100")
            else:
                url = (f"https://www.reddit.com/r/{subreddit}/search.json"
                       f"?q={urllib.parse.quote(q)}&restrict_sr=on"
                       f"&sort={sort}&t={time_filter}&limit=100")
        else:
            url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?t={time_filter}&limit=100"
        if after:
            url += f"&after={after}"
        time.sleep(RATE_LIMIT_SEC)
        data = _request(url)
        children = data.get("data", {}).get("children", [])
        if not children:
            break
        for child in children:
            posts.append(child.get("data", {}))
            fetched += 1
            if fetched >= limit:
                break
        after = data.get("data", {}).get("after")
        if not after:
            break
    return posts[:limit]


def normalize_body(text: str) -> str:
    """Apply noise stripping + anonymization."""
    if not text:
        return ""
    out = text
    # Strip noise patterns
    for pat in NOISE_PATTERNS:
        out = pat.sub("", out)
    # Anonymize usernames
    out = USERNAME_PATTERN.sub("[USER]", out)
    # Collapse multiple blank lines
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def is_rules_question(title: str, body: str) -> bool:
    """Heuristic: skip posts that are clearly rules-lookup questions."""
    head = (title + " " + body[:300]).strip()
    for pat in RULES_QUESTION_PATTERNS:
        if pat.search(head):
            # Title-only "can a rogue X" → reject; if body is long narrative, accept
            if len(body.split()) < MIN_WORDS * 1.5:
                return True
    return False


def count_named_entity_proxies(text: str) -> int:
    """Count tokens that look like proper nouns (capitalized, mid-sentence)."""
    sentences = re.split(r"[.!?]\s+", text)
    cap_tokens = set()
    for s in sentences:
        words = s.split()
        for i, w in enumerate(words):
            # skip first word of sentence (start-of-sentence cap)
            if i == 0:
                continue
            stripped = re.sub(r"[^\w]", "", w)
            if stripped and stripped[0].isupper() and len(stripped) > 2:
                cap_tokens.add(stripped)
    return len(cap_tokens)


def accept_post(post: dict) -> tuple:
    """Decide whether to keep post. Returns (accept, reason)."""
    body = post.get("selftext", "") or ""
    title = post.get("title", "") or ""
    if not body:
        return False, "no selftext (likely link post)"
    word_count = len(body.split())
    if word_count < MIN_WORDS:
        return False, f"too short ({word_count} words)"
    cap_count = count_named_entity_proxies(body)
    if cap_count < MIN_CAPITALIZED_TOKENS:
        return False, f"too few named-entity proxies ({cap_count})"
    if is_rules_question(title, body):
        return False, "rules question"
    return True, "ok"


def save_post(post: dict, out_dir: pathlib.Path) -> Optional[pathlib.Path]:
    body = normalize_body(post.get("selftext", ""))
    if not body:
        return None
    post_id = post.get("id", "unknown")
    title = post.get("title", "(no title)").strip()
    flair = post.get("link_flair_text") or "(no flair)"
    subreddit = post.get("subreddit", "?")
    score = post.get("score", 0)
    created = post.get("created_utc", 0)
    out_path = out_dir / f"{post_id}.md"
    header = (f"# {title}\n\n"
              f"**Subreddit:** r/{subreddit}  **Flair:** {flair}  "
              f"**Score:** {score}  **Created (utc):** {int(created)}\n"
              f"**Post ID:** {post_id}\n\n"
              f"---\n\n")
    out_path.write_text(header + body, encoding="utf-8")
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subreddit", required=True,
                    help='subreddit name, or "all" for cross-Reddit search')
    ap.add_argument("--flair", default=None, help="optional flair filter")
    ap.add_argument("--query", default=None,
                    help="optional phrase query (uses Reddit search endpoint)")
    ap.add_argument("--sort", default="top", choices=["top", "hot", "new", "best"])
    ap.add_argument("--time", dest="time_filter", default="year",
                    choices=["hour", "day", "week", "month", "year", "all"])
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--out-dir", required=True, type=pathlib.Path)
    args = ap.parse_args()

    out_dir = args.out_dir.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[collect] r/{args.subreddit} flair={args.flair!r} query={args.query!r} "
          f"sort={args.sort} t={args.time_filter} limit={args.limit}")
    print(f"[collect] out → {out_dir}")

    posts = fetch_listing(args.subreddit, args.flair, args.sort, args.time_filter,
                          args.limit, query=args.query)
    print(f"[collect] fetched {len(posts)} stubs from listing")

    accepted = 0
    rejected = {"no selftext (likely link post)": 0, "too short": 0,
                "too few named-entity proxies": 0, "rules question": 0}
    for post in posts:
        ok, reason = accept_post(post)
        if not ok:
            # Bucket the reason
            for key in rejected:
                if reason.startswith(key.split(" (")[0]):
                    rejected[key] = rejected.get(key, 0) + 1
                    break
            else:
                rejected[reason] = rejected.get(reason, 0) + 1
            continue
        path = save_post(post, out_dir)
        if path:
            accepted += 1
    print(f"[collect] accepted {accepted}/{len(posts)}")
    for k, v in rejected.items():
        if v:
            print(f"  reject: {k} → {v}")
    # Summary log
    summary = {
        "subreddit": args.subreddit,
        "flair": args.flair,
        "sort": args.sort,
        "time_filter": args.time_filter,
        "fetched": len(posts),
        "accepted": accepted,
        "rejected": rejected,
        "out_dir": str(out_dir),
    }
    (out_dir / "_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                           encoding="utf-8")


if __name__ == "__main__":
    main()
