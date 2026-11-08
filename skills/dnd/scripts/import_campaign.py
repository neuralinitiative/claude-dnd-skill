#!/usr/bin/env python3
"""
import_campaign.py — extract raw text from a campaign source file for /dnd import.

Supported input formats:
  .pdf      — PyMuPDF column-aware extraction (recommended: pip3 install pymupdf).
              Multi-column books (most published modules) are de-columned by
              sorting text blocks into reading order, so chapter/section structure
              survives for segmentation. Falls back to poppler's pdftotext in
              reading-order mode when PyMuPDF is unavailable.
  .md       — read directly (plain markdown)
  .txt      — read directly
  .docx     — python-docx extraction

Usage:
  python3 import_campaign.py <filepath>            # print full extracted text
  python3 import_campaign.py <filepath> --info     # print file info + page/word count only
  python3 import_campaign.py <filepath> --chunk N  # print chunk N (~4000 words of Latin text,
                                                   #  ~6000 characters of CJK; line breaks kept)
  python3 import_campaign.py <filepath> --chunks   # print total number of chunks

Output is UTF-8 plain text, written to stdout. Claude reads this and maps it to campaign files.
"""

import sys
import os
import argparse
import re
import subprocess
import textwrap
import unicodedata

# This script prints full module text (often Chinese) to stdout. Without this,
# a GBK console/pipe raises UnicodeEncodeError on the first non-ASCII line.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CHUNK_WORDS = 4000  # nominal words per chunk, kept for --info

# Chunks are measured in WEIGHT, not words. Splitting on whitespace counted a
# Chinese or Japanese module as a handful of "words" -- a 48,000-character
# Chinese module was 1 word and 1 chunk, a single 144 KB read -- and rejoining
# with " ".join flattened every module's lines, so headings, tables and stat
# blocks arrived as one line. A character weighs 1, and a wide (CJK, Hangul)
# character weighs 4: English averages about six characters per word with its
# space, so 24,000 weight is ~4,000 English words or ~6,000 CJK characters --
# comparable reads either way.
CHUNK_WEIGHT = 24000
WIDE_WEIGHT = 4


def order_blocks(blocks, page_width, gutter_frac=0.05, min_col_share=0.15):
    """Sort text blocks into human reading order, de-columning multi-column pages.

    `blocks` is a list of (x0, y0, x1, y1, text) tuples (PyMuPDF block geometry).
    Returns the block texts in reading order.

    Why this matters: published modules are typically two-column. `pdftotext
    -layout` preserves physical layout and interleaves the columns (scrambling
    reading order and burying section headers mid-line), which makes the model
    collapse the whole book into one chapter at import. Sorting blocks by column
    then vertical position restores the order the author intended, so section
    headers and keyed encounters come out clean and in sequence.

    Algorithm:
      - Classify each block as left / right / full-width (spans the centre gutter).
      - If one side is trivial, treat the page as a single column (sort by y).
      - Otherwise, full-width blocks act as band dividers (full-width headers
        belong between the columns above and below them); within each band emit
        the left column top-to-bottom, then the right column.
    """
    blocks = [b for b in blocks if b[4] and b[4].strip()]
    if not blocks:
        return []
    if page_width <= 0:
        return [b[4] for b in sorted(blocks, key=lambda b: (round(b[1], 1), b[0]))]

    mid = page_width / 2.0
    tol = page_width * gutter_frac
    left, right, full = [], [], []
    for b in blocks:
        x0, x1 = b[0], b[2]
        if x0 < mid - tol and x1 > mid + tol:
            full.append(b)
        elif x1 <= mid + tol:
            left.append(b)
        elif x0 >= mid - tol:
            right.append(b)
        else:  # straddles slightly — assign by centre
            (left if (x0 + x1) / 2.0 < mid else right).append(b)

    total = len(blocks)
    threshold = max(1, min_col_share * total)
    if len(left) < threshold or len(right) < threshold:
        # Effectively single-column (e.g. title pages, full-width prose).
        return [b[4] for b in sorted(blocks, key=lambda b: (round(b[1], 1), b[0]))]

    full_sorted = sorted(full, key=lambda b: b[1])
    band_edges = [(-float("inf"))] + [(f[1] + f[3]) / 2.0 for f in full_sorted] + [float("inf")]

    def cy(b):
        return (b[1] + b[3]) / 2.0

    ordered = []
    for i in range(len(full_sorted) + 1):
        lo, hi = band_edges[i], band_edges[i + 1]
        band_left = sorted((b for b in left if lo <= cy(b) < hi), key=lambda b: b[1])
        band_right = sorted((b for b in right if lo <= cy(b) < hi), key=lambda b: b[1])
        ordered.extend(b[4] for b in band_left)
        ordered.extend(b[4] for b in band_right)
        if i < len(full_sorted):
            ordered.append(full_sorted[i][4])
    return ordered


def _extract_pdf_pymupdf(path: str) -> str:
    """Column-aware extraction via PyMuPDF. Returns reading-order text or ''."""
    import fitz  # PyMuPDF
    doc = fitz.open(path)
    pages = []
    for page in doc:
        raw = page.get_text("blocks")  # (x0, y0, x1, y1, text, block_no, block_type)
        blocks = [
            (b[0], b[1], b[2], b[3], b[4])
            for b in raw
            if len(b) >= 7 and b[6] == 0 and b[4] and b[4].strip()
        ]
        ordered = order_blocks(blocks, page.rect.width)
        if ordered:
            pages.append("\n\n".join(ordered))
    return "\n\n".join(pages)


def extract_pdf(path: str) -> str:
    """Extract PDF text in reading order.

    Primary path is PyMuPDF column-aware extraction (correct for multi-column
    modules). Falls back to poppler's pdftotext in *reading-order* mode — never
    `-layout`, which interleaves columns and breaks chapter segmentation.
    """
    # Primary: PyMuPDF column-aware
    try:
        import fitz  # noqa: F401
        have_fitz = True
    except ImportError:
        have_fitz = False

    if have_fitz:
        try:
            text = _extract_pdf_pymupdf(path)
            if text.strip():
                return text
            print("[import] PyMuPDF returned no text; trying pdftotext.", file=sys.stderr)
        except Exception as e:  # corrupt/edge PDFs — degrade rather than crash
            print(f"[import] PyMuPDF extraction failed ({e}); trying pdftotext.", file=sys.stderr)

    # Fallback: pdftotext reading-order mode (NOT -layout)
    try:
        result = subprocess.run(
            ["pdftotext", path, "-"],
            capture_output=True, encoding="utf-8", text=True, timeout=60
        )
        if result.returncode == 0 and result.stdout.strip():
            if not have_fitz:
                print(
                    "[import] Using pdftotext fallback. For reliable multi-column "
                    "extraction (most published modules), install PyMuPDF: "
                    "pip3 install pymupdf",
                    file=sys.stderr,
                )
            return result.stdout
    except FileNotFoundError:
        pass

    raise RuntimeError(
        "PDF extraction failed. Install PyMuPDF (recommended): pip3 install pymupdf\n"
        "or install poppler's pdftotext: brew install poppler"
    )


def strip_obsidian_frontmatter(text: str) -> str:
    """Remove YAML frontmatter block (common in markdown files)."""
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            return text[end + 3:].lstrip()
    return text


def extract_docx(path: str) -> str:
    try:
        from docx import Document
        doc = Document(path)
        return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except ImportError:
        raise RuntimeError(
            "python-docx not installed. Run: pip3 install python-docx"
        )


def extract(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()

    if ext == ".pdf":
        return extract_pdf(path)
    elif ext in (".md", ".txt", ".markdown"):
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        return strip_obsidian_frontmatter(text)
    elif ext == ".docx":
        return extract_docx(path)
    else:
        # Unknown extension — try reading as plain text
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as e:
            raise RuntimeError(f"Cannot read {path}: {e}")


def word_count(text: str) -> int:
    return len(text.split())


def _weight(ch: str) -> int:
    return WIDE_WEIGHT if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def split_chunks(text: str, budget: int = CHUNK_WEIGHT) -> list:
    """Split text into chunks of at most `budget` weight, on line boundaries.

    Lines are kept whole, newlines included, so "".join(chunks) == text. A
    single line heavier than the budget (a paragraph of CJK prose with no
    breaks) is cut by characters rather than returned oversized.
    """
    chunks, cur, cur_w = [], [], 0
    for line in text.splitlines(keepends=True):
        w = sum(_weight(c) for c in line)
        if w > budget:
            # Too heavy to keep whole: top up the current chunk with it, then
            # carry on in budget-sized pieces.
            for c in line:
                cw = _weight(c)
                if cur and cur_w + cw > budget:
                    chunks.append("".join(cur))
                    cur, cur_w = [], 0
                cur.append(c)
                cur_w += cw
            continue
        if cur and cur_w + w > budget:
            chunks.append("".join(cur))
            cur, cur_w = [], 0
        cur.append(line)
        cur_w += w
    if cur:
        chunks.append("".join(cur))
    return chunks or [""]


def chunk_text(text: str, chunk_index: int) -> str:
    """Return chunk N (0-indexed), with the source's line structure intact."""
    chunks = split_chunks(text)
    if chunk_index < 0 or chunk_index >= len(chunks):
        return ""
    return chunks[chunk_index]


def total_chunks(text: str) -> int:
    return len(split_chunks(text))


def file_info(path: str, text: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    wc = word_count(text)
    chunks = total_chunks(text)
    lines = [
        f"File:   {os.path.basename(path)}",
        f"Type:   {ext or 'unknown'}",
        f"Words:  {wc:,}",
        f"Chars:  {len(text):,}",
        f"Chunks: {chunks}  (--chunk 0 through --chunk {chunks - 1})",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Extract text from campaign source file.")
    parser.add_argument("filepath", help="Path to the source file")
    parser.add_argument("--info", action="store_true", help="Print file info only")
    parser.add_argument("--chunks", action="store_true", help="Print total chunk count")
    parser.add_argument("--chunk", type=int, default=None, metavar="N",
                        help="Print chunk N (0-indexed; ~4000 words or ~6000 CJK characters)")
    args = parser.parse_args()

    if not os.path.exists(args.filepath):
        print(f"Error: file not found: {args.filepath}", file=sys.stderr)
        sys.exit(1)

    try:
        text = extract(args.filepath)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not text.strip():
        print("Warning: extracted text is empty. File may be image-only PDF.", file=sys.stderr)
        sys.exit(1)

    if args.info:
        print(file_info(args.filepath, text))
    elif args.chunks:
        print(total_chunks(text))
    elif args.chunk is not None:
        chunk = chunk_text(text, args.chunk)
        if not chunk:
            print(f"Error: chunk {args.chunk} out of range (max: {total_chunks(text) - 1})",
                  file=sys.stderr)
            sys.exit(1)
        print(chunk)
    else:
        # Print full text
        print(text)


if __name__ == "__main__":
    main()
