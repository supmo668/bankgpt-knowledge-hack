"""Deck checks (HACA-8): figures are reproducible from the committed numbers, and nothing in docs/deck
quotes snapshot page text or the public eval's question or answer text (the repo is public)."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import unittest
import xml.dom.minidom
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import deck_figures  # noqa: E402

DECK = ROOT / "docs/deck"
SHINGLE = 8  # words; a run this long shared with a page or an eval answer is a quote, not a coincidence


def deck_text() -> str:
    """Every committed text file under docs/deck (markdown, SVG, JSON), lowercased."""
    out = []
    for p in sorted(DECK.rglob("*")):
        if p.is_file() and p.suffix in {".md", ".svg", ".json", ".html", ".txt"}:
            out.append(p.read_text(encoding="utf-8"))
    return "\n".join(out).lower()


def words(s: str) -> list[str]:
    """Lowercased word tokens; a trailing full stop is punctuation ("2026." is "2026"), an inner one is kept."""
    return [w for w in (t.rstrip(".") for t in re.findall(r"[a-z0-9$%.]+", s.lower())) if w]


def shingles(s: str, n: int = SHINGLE) -> set[tuple[str, ...]]:
    w = words(s)
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


def eval_items(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def eval_leaks(deck: str, items: list[dict], n: int = 6) -> list[str]:
    """Public-eval text the deck quotes. A question or answer of n words or more leaks if it shares an n-word run
    with the deck; a shorter one (e.g. a bare number or a URL) leaks if the deck contains it as a whole phrase.
    A must_include / must_not_include value with a digit leaks if it appears verbatim."""
    deck_sh, deck_w = shingles(deck, n), f" {' '.join(words(deck))} "
    hits = []
    for it in items:
        for field in ("expected_answer", "question"):
            w = words(it.get(field) or "")
            if len(w) >= n:
                if shingles(" ".join(w), n) & deck_sh:
                    hits.append(f"{it['id']} {field}")
            elif w and len(" ".join(w)) >= 4 and f" {' '.join(w)} " in deck_w:
                hits.append(f"{it['id']} {field}")
        for value in it.get("must_include", []) + it.get("must_not_include", []):
            if len(value) >= 4 and re.search(r"\d", value) and value.lower() in deck.lower():
                hits.append(f"{it['id']} value {value!r}")
    return hits


def page_leaks(deck: str, pages: dict[str, str]) -> list[str]:
    """Snapshot pages that share an 8-word run with the deck."""
    deck_sh = shingles(deck)
    return [name for name, page in pages.items() if shingles(page) & deck_sh]


class Inputs(unittest.TestCase):
    def test_progression_parses_baseline_first_and_latest_last(self):
        prog = deck_figures.score_progression(deck_figures.RESULTS.read_text())
        self.assertEqual(prog[0][0], "starter")
        self.assertAlmostEqual(prog[0][2], deck_figures.BASELINE)
        self.assertGreater(prog[-1][2], prog[0][2])
        self.assertGreaterEqual(len(prog), 3)

    def test_progression_stops_at_the_end_of_its_table(self):
        md = ("## Score per run\n\n| Commit | Change | Public final |\n|---|---|---|\n| starter | s | **0.093** |\n"
              "| `abc1234` | v1 | 0.455 |\n\n## Per-category\n\n| Category | n | Score |\n|---|---|---|\n"
              "| current_value | 3 | 0.33 |\n")
        self.assertEqual(deck_figures.score_progression(md), [("starter", "s", 0.093), ("abc1234", "v1", 0.455)])
        real = deck_figures.score_progression(deck_figures.RESULTS.read_text())
        for commit, _, _ in real:
            self.assertRegex(commit, r"^(starter|[0-9a-f]{7,})$")

    def test_progression_rejects_a_missing_table(self):
        with self.assertRaises(ValueError):
            deck_figures.score_progression("# no table here\n")

    def test_progression_does_not_borrow_the_next_sections_table(self):
        md = ("## Score per run\n\nTable moved.\n\n## Per-category\n\n| Category | n | Score |\n|---|---|---|\n"
              "| current_value | 3 | 0.33 |\n")
        with self.assertRaises(ValueError):
            deck_figures.score_progression(md)

    def test_latest_runs_keeps_the_last_row_per_suite(self):
        rows = [{"utc": "a", "commit": "c1", "suite": "public", "final": "0.1", "accuracy": "0.2", "p95_s": "0.1",
                 "by_category": "{}"},
                {"utc": "b", "commit": "c2", "suite": "public", "final": "0.5", "accuracy": "0.6", "p95_s": "0.1",
                 "by_category": '{"x": 1.0}'}]
        got = deck_figures.latest_runs(rows)["public"]
        self.assertEqual((got["commit"], got["final"], got["by_category"]), ("c2", 0.5, {"x": 1.0}))

    def test_percentile_matches_the_kit_scorer(self):
        sys.path.insert(0, str(ROOT / "harness"))
        from scoring import score_speed
        xs = [0.01 * i for i in range(1, 41)]
        self.assertEqual(round(deck_figures.percentile(xs, 0.95), 2), score_speed(xs)["p95_s"])

    def test_scorer_output_matches_runs_csv(self):
        """The latency/pillar run and runs.csv must be the same system: same final score per suite."""
        scorer = json.loads(deck_figures.SCORER.read_text())["suites"]
        with deck_figures.RUNS.open(newline="") as f:
            latest = deck_figures.latest_runs(list(csv.DictReader(f)))
        for suite, rep in scorer.items():
            self.assertAlmostEqual(rep["final"], latest[suite]["final"], places=3, msg=suite)
            strings = [v for v in _leaves(rep) if isinstance(v, str)]
            self.assertEqual(strings, [], f"{suite}: scorer_output must hold numbers only")


def _leaves(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from _leaves(v)
    elif isinstance(x, list):
        for v in x:
            yield from _leaves(v)
    else:
        yield x


class Figures(unittest.TestCase):
    def test_committed_figures_are_up_to_date_and_valid_svg(self):
        built = deck_figures.build_all()
        for name, doc in built.items():
            xml.dom.minidom.parseString(doc)  # well-formed
            committed = deck_figures.FIG / name
            self.assertTrue(committed.exists(), f"{name} missing: run scripts/deck_figures.py")
            self.assertEqual(committed.read_text(), doc, f"{name} is stale: run scripts/deck_figures.py")

    def test_every_figure_has_an_accessible_title_and_description(self):
        for name, doc in deck_figures.build_all().items():
            self.assertRegex(doc, r'<title id="t">[^<]+</title>', name)
            self.assertRegex(doc, r'<desc id="d">[^<]+</desc>', name)

    def test_deck_embeds_every_figure(self):
        md = (DECK / "deck.md").read_text()
        for name in deck_figures.build_all():
            self.assertIn(f"figures/{name}", md)


class Pdf(unittest.TestCase):
    def test_pdf_has_one_page_per_slide(self):
        md = re.sub(r"<!--.*?-->", "", (DECK / "deck.md").read_text(), flags=re.S)
        slides = [s for s in re.split(r"^---\s*$", md, flags=re.M) if s.strip()]
        pdf = (DECK / "deck.pdf").read_bytes()
        self.assertEqual(len(re.findall(rb"/Type /Page\b", pdf)), len(slides),
                         "deck.pdf is stale: cd scripts/deck && node build_pdf.mjs")

    def test_pdf_was_built_from_the_committed_deck_and_figures(self):
        """build_pdf.mjs stamps a sha256 of deck.md + figures/*.svg (sorted) into the PDF Keywords."""
        h = hashlib.sha256((DECK / "deck.md").read_bytes())
        for f in sorted(p.name for p in (DECK / "figures").glob("*.svg")):
            h.update(b"\0" + f.encode() + b"\0")
            h.update((DECK / "figures" / f).read_bytes())
        self.assertIn(f"(source-sha256-{h.hexdigest()})".encode(), (DECK / "deck.pdf").read_bytes(),
                      "deck.pdf is stale: cd scripts/deck && node build_pdf.mjs")


class NoLeak(unittest.TestCase):
    """The rules forbid redistributing the site; the public eval answers are the organisers'."""

    def test_no_public_eval_answer_or_question_text(self):
        deck = deck_text()
        self.assertTrue(shingles(deck))
        self.assertEqual(eval_leaks(deck, eval_items(ROOT / "eval/public_eval.jsonl")), [])

    def test_no_snapshot_page_text(self):
        snaps = Path(os.environ.get("BANKGPT_SNAPSHOTS", ROOT / "snapshots"))
        pages = sorted(snaps.glob("*/pages/*.md")) if snaps.is_dir() else []
        if not pages:
            self.skipTest("snapshots not unpacked (gitignored); run the eval once to check page text")
        texts = {f"{p.parent.parent.name}/{p.name}": p.read_text(encoding="utf-8", errors="ignore") for p in pages}
        self.assertEqual(page_leaks(deck_text(), texts), [])


class LeakCheckCatches(unittest.TestCase):
    """Positive controls: the same functions the NoLeak tests call must flag a planted quote."""

    @classmethod
    def setUpClass(cls):
        cls.items = eval_items(ROOT / "eval/public_eval.jsonl")
        cls.deck = deck_text()

    def planted(self, quote: str) -> str:
        return self.deck + "\nas the slide says, " + quote + " and so on.\n"

    def test_a_long_answer(self):
        it = max(self.items, key=lambda i: len(words(i["expected_answer"])))
        self.assertIn(f"{it['id']} expected_answer", eval_leaks(self.planted(it["expected_answer"]), self.items))

    def test_a_partial_long_answer(self):
        it = max(self.items, key=lambda i: len(words(i["expected_answer"])))
        part = " ".join(words(it["expected_answer"])[3:9])
        self.assertIn(f"{it['id']} expected_answer", eval_leaks(self.planted(part), self.items))

    def test_every_short_answer(self):
        short = [i for i in self.items if 0 < len(words(i["expected_answer"])) < 6
                 and len(" ".join(words(i["expected_answer"]))) >= 4]
        self.assertTrue(short, "the public set has short answers; this control must exercise them")
        for it in short:
            self.assertIn(f"{it['id']} expected_answer", eval_leaks(self.planted(it["expected_answer"].upper()),
                                                                    self.items), it["id"])

    def test_a_question(self):
        it = self.items[0]
        self.assertIn(f"{it['id']} question", eval_leaks(self.planted(it["question"]), self.items))

    def test_a_site_value(self):
        it = next(i for i in self.items if any(re.search(r"\d", v) and len(v) >= 4 for v in i["must_include"]))
        value = next(v for v in it["must_include"] if re.search(r"\d", v) and len(v) >= 4)
        self.assertIn(f"{it['id']} value {value!r}", eval_leaks(self.planted(f"at {value} today"), self.items))

    def test_snapshot_page_text(self):
        page = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo"
        self.assertEqual(page_leaks(self.planted(" ".join(page.split()[1:10])), {"x/p.md": page}), ["x/p.md"])
        self.assertEqual(page_leaks(self.planted(" ".join(page.split()[:7])), {"x/p.md": page}), [])

if __name__ == "__main__":
    unittest.main()
