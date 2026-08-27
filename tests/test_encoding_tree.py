"""
test_encoding_tree.py — tree-walk guard against bare text-mode open() calls.

On Chinese Windows (cp936/GBK) a bare text-mode open() mis-decodes UTF-8 files
and can silently corrupt data — the bug class fixed by the 2026-08 UTF-8 sweep.
This test walks the shipped tree (skills/, scripts/, dice-server/, tests/) so
the bug cannot come back through a new call site. The bash launchers are
checked too: PYTHONUTF8=1 must be exported so every spawned python process is
covered regardless of call-site discipline.

read/write_text calls are judged by their full call span (paren-balanced and
string-aware), so a multi-line write_text(dedent("..."), encoding="utf-8")
is accepted even when the keyword lands several lines after the call starts.
The utf8io wrapper (read_text(path)) is exempt: it is the lossless helper
that makes the sweep possible, and `from utf8io import read_text` uses are
bare calls without a leading dot, so they never match the method-call regex
anyway.

Run from repo root:
    python3 -m unittest tests.test_encoding_tree -v
"""
import io
import pathlib
import re
import tokenize
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
# tests/ and scripts/graph/ are scanned too: the fixes landed there as well,
# and leaving them out of the scan would let a bare open() come back unseen.
SCAN_ROOTS = [SKILL, REPO / "scripts", REPO / "dice-server", REPO / "tests"]
_SKIP_DIRS = {"__pycache__", ".venv", "venv", ".git", "node_modules"}

# Arguments may span lines, so each candidate is judged against a 3-line window.
_OPEN_NO_ENC_RE = re.compile(
    r"\bopen\s*\(\s*(?P<arg>[^,\)]+)(?P<rest>(?:(?!encoding)[^)]){0,200})\)",
    re.S,
)


def _read_write_calls_without_encoding(src: str) -> list[int]:
    """Line numbers of .read_text()/.write_text() calls whose call span
    (paren-balanced, tokenized) contains no `encoding=` keyword.

    Uses Python's own tokenizer, so strings, comments, f-strings and regex
    literals are handled correctly: call text inside a docstring or a regex
    is a STRING token, not a call. The utf8io wrapper (utf8io.read_text) is
    exempt — it is the lossless helper from the sweep; bare helper calls
    (`read_text(path)`) are not preceded by a `.` token, so they never match.
    """
    tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    bad: list[int] = []
    for i, tok in enumerate(tokens):
        if tok.type != tokenize.NAME or tok.string not in ("read_text", "write_text"):
            continue
        if not (i > 0 and tokens[i - 1].type == tokenize.OP and tokens[i - 1].string == "."):
            continue  # not a pathlib method call
        if i > 1 and tokens[i - 2].type == tokenize.NAME and tokens[i - 2].string == "utf8io":
            continue  # safe wrapper — lossless by construction
        depth = 0
        has_encoding = False
        j = i + 1
        while j < len(tokens):
            t = tokens[j]
            if t.type == tokenize.NAME and t.string == "encoding":
                nxt = tokens[j + 1] if j + 1 < len(tokens) else None
                # depth == 1 is THIS call's argument list. Without that test a
                # keyword belonging to a NESTED call satisfies the outer one, so
                # `dst.write_text(src.read_text(encoding="utf-8"))` reads as
                # covered while the write is still bare.
                if nxt is not None and nxt.type == tokenize.OP and nxt.string == "=" and depth == 1:
                    has_encoding = True
            if t.type == tokenize.OP:
                if t.string == "(":
                    depth += 1
                elif t.string == ")":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        if not has_encoding:
            bad.append(tok.start[0])
    return bad


def _scan() -> list[str]:
    issues = []
    for base in SCAN_ROOTS:
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file() or f.suffix not in (".py", ".sh"):
                continue
            # Judge paths relative to REPO, not absolute — "graph" (or any
            # skip dir) in the checkout's absolute path must not zero out the
            # scan depending on where the repo happens to be cloned.
            rel = f.relative_to(REPO).parts
            if any(p in _SKIP_DIRS for p in rel):
                continue
            src = f.read_text(encoding="utf-8", errors="replace")
            lines = src.splitlines()
            for i, line in enumerate(lines, 1):
                window = "\n".join(lines[i - 1 : i + 3])
                m = _OPEN_NO_ENC_RE.search(line)
                if (
                    m
                    and "encoding" not in window
                    and "rb" not in window
                    and "wb" not in window
                    and "os.open" not in line    # fd ops — no Python-level encoding
                    and "fitz.open" not in line  # PyMuPDF binary open
                ):
                    issues.append(
                        f"{f.relative_to(REPO)}:{i}  open() without encoding: {line.strip()[:90]}"
                    )
            if f.suffix == ".py":
                for ln in _read_write_calls_without_encoding(src):
                    issues.append(
                        f"{f.relative_to(REPO)}:{ln}  read/write_text call span has no encoding="
                    )
    return issues


class EncodingTreeTests(unittest.TestCase):

    def test_no_bare_text_mode_io(self):
        issues = _scan()
        self.assertEqual([], issues, "bare text-mode IO without encoding= found:\n" + "\n".join(issues))

    def test_launchers_export_pythonutf8(self):
        """Bash launchers must export PYTHONUTF8=1 so spawned python processes
        are covered regardless of what the call sites say."""
        for sh in ("start-display.sh", "verify_tail.sh"):
            path = SKILL / "display" / sh
            if not path.exists():
                self.fail(f"expected launcher missing: {path}")
            self.assertIn(
                "PYTHONUTF8=1",
                path.read_text(encoding="utf-8"),
                f"{sh} should export PYTHONUTF8=1",
            )


if __name__ == "__main__":
    unittest.main()


class NestedCallSpans(unittest.TestCase):
    """A keyword on an inner call must not cover an outer bare one.

    `session_recap.py` writes `dst.write_text(src.read_text(encoding="utf-8"),
    encoding="utf-8")` twice, so the shape is real and both halves genuinely
    need their own keyword. Judging the span without a depth test made the
    inner one count for the outer, which is the quiet way this scan would stop
    seeing a whole class of site.
    """

    def test_inner_keyword_does_not_cover_an_outer_bare_call(self) -> None:
        self.assertEqual(
            _read_write_calls_without_encoding(
                'dst.write_text(src.read_text(encoding="utf-8"))\n'
            ),
            [1],
        )

    def test_both_keywords_present_is_still_clean(self) -> None:
        # The real session_recap.py shape. If the depth test cried wolf here it
        # would be worse than the hole it closes.
        self.assertEqual(
            _read_write_calls_without_encoding(
                'dst.write_text(src.read_text(encoding="utf-8"),\n'
                '               encoding="utf-8")\n'
            ),
            [],
        )

    def test_a_keyword_on_the_closing_line_still_counts(self) -> None:
        self.assertEqual(
            _read_write_calls_without_encoding(
                'p.write_text(\n    dedent("""x"""),\n    encoding="utf-8",\n)\n'
            ),
            [],
        )
