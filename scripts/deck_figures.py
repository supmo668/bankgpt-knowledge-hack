#!/usr/bin/env python3
"""Draw the L2 deck figures as standalone SVG (standard library only).

    python scripts/deck_figures.py            # writes docs/deck/figures/*.svg

Inputs, all already in the repo and all numbers only:
  docs/eval/RESULTS.md                 the score-per-commit table (public set, kit scorer)
  docs/eval/runs.csv                   the newest row per suite (final, accuracy, per-category)
  docs/deck/data/scorer_output.json    per-question latencies and pillars (scripts/deck_measure.py)
No snapshot page text and no eval question or answer text goes into any figure.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "docs/eval/RESULTS.md"
RUNS = ROOT / "docs/eval/runs.csv"
SCORER = ROOT / "docs/deck/data/scorer_output.json"
FIG = ROOT / "docs/deck/figures"

BASELINE = 0.093
SUITE_ORDER = ("public", "dev", "blind", "selftest")
SUITE_LABEL = {"public": "public (40, kit)", "dev": "dev (30, tuned on)", "blind": "blind (18, held out)",
               "selftest": "selftest (5, synthetic)"}

# Reference palette (dataviz skill, light mode). Categorical slots 1-2 in fixed order; text in ink tokens.
SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e4e3df"
S1, S2 = "#2a78d6", "#eb6834"
FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


# ── inputs ────────────────────────────────────────────────────────────────

def score_progression(md: str) -> list[tuple[str, str, float]]:
    """(commit, change, public final) rows of the 'Score per run' table in RESULTS.md, in table order."""
    # The heading, then prose lines (no table rows, no other heading), then the first run of table lines. A
    # section without its own table must not borrow the next section's table.
    m = re.search(r"^## Score per run[^\n]*\n(?:(?![|#])[^\n]*\n)*((?:\|[^\n]*\n)+)", md, flags=re.M)
    if not m:
        raise ValueError("RESULTS.md has no 'Score per run' table")
    out = []
    for line in m.group(1).splitlines()[2:]:  # skip header + rule
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        score = re.search(r"\d+\.\d+", cells[2])
        if score:
            out.append((cells[0].strip("`"), cells[1], float(score.group())))
    if not out:
        raise ValueError("RESULTS.md 'Score per run' table has no scored rows")
    return out


def latest_runs(rows: list[dict]) -> dict[str, dict]:
    """Newest runs.csv row per suite (rows are appended in time order, so the last one wins)."""
    out = {}
    for r in rows:
        out[r["suite"]] = {"utc": r["utc"], "commit": r["commit"], "final": float(r["final"]),
                           "accuracy": float(r["accuracy"]), "p95_s": float(r["p95_s"]),
                           "by_category": json.loads(r["by_category"])}
    return out


def percentile(xs: list[float], q: float) -> float:
    """The kit scorer's percentile (harness/scoring.py score_speed), so the figure matches its p95."""
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


# ── SVG helpers ───────────────────────────────────────────────────────────

def svg(w: int, h: int, body: list[str], title: str, desc: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'role="img" aria-labelledby="t d" font-family="{FONT}">\n'
            f'<title id="t">{escape(title)}</title>\n<desc id="d">{escape(desc)}</desc>\n'
            f'<rect width="{w}" height="{h}" fill="{SURFACE}"/>\n' + "\n".join(body) + "\n</svg>\n")


def text(x: float, y: float, s: str, size: int = 13, fill: str = INK2, anchor: str = "start",
         weight: str = "normal") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
            f'font-weight="{weight}">{escape(s)}</text>')


def heading(title: str, sub: str) -> list[str]:
    return [text(32, 40, title, 20, INK, weight="600"), text(32, 64, sub, 13, INK2)]


def bar_h(x: float, y: float, w: float, h: float, fill: str, tip: str) -> str:
    """Horizontal bar, square at the baseline, 4px rounded data end; <title> is the hover tooltip."""
    w = max(w, 0.0)
    r = min(4.0, w / 2, h / 2)
    d = (f"M{x:.1f},{y:.1f} H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} "
         f"V{y + h - r:.1f} Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f} H{x:.1f} Z")
    return f'<path d="{d}" fill="{fill}"><title>{escape(tip)}</title></path>'


def bar_v(x: float, base: float, w: float, h: float, fill: str, tip: str) -> str:
    """Vertical bar anchored at the baseline, 4px rounded top."""
    h = max(h, 0.0)
    r = min(4.0, w / 2, h / 2)
    top = base - h
    d = (f"M{x:.1f},{base:.1f} V{top + r:.1f} Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f} H{x + w - r:.1f} "
         f"Q{x + w:.1f},{top:.1f} {x + w:.1f},{top + r:.1f} V{base:.1f} Z")
    return f'<path d="{d}" fill="{fill}"><title>{escape(tip)}</title></path>'


# ── figures ───────────────────────────────────────────────────────────────

def fig_score_per_run(prog: list[tuple[str, str, float]]) -> str:
    W, H, L, R, T, B = 960, 500, 72, 40, 96, 120
    pw, ph = W - L - R, H - T - B
    xs = [L + pw * i / (len(prog) - 1) for i in range(len(prog))]
    y = lambda v: T + ph * (1 - v)
    body = heading("Public score per commit, from the starter's 0.093",
                   "Kit scorer, 40 public questions (no conflicts gold in the kit). Source: docs/eval/RESULTS.md")
    for g in (0, 0.25, 0.5, 0.75, 1.0):
        body.append(f'<line x1="{L}" x2="{W - R}" y1="{y(g):.1f}" y2="{y(g):.1f}" stroke="{GRID}"/>')
        body.append(text(L - 10, y(g) + 4, f"{g:.2f}", 12, MUTED, "end"))
    body.append(f'<line x1="{L}" x2="{W - R}" y1="{y(BASELINE):.1f}" y2="{y(BASELINE):.1f}" stroke="{INK2}" '
                f'stroke-dasharray="6 4" stroke-width="1.5"/>')
    body.append(text(W - R, y(BASELINE) - 8, f"starter baseline {BASELINE}", 12, INK2, "end"))
    pts = " ".join(f"{x:.1f},{y(v):.1f}" for x, (_, _, v) in zip(xs, prog))
    body.append(f'<polyline points="{pts}" fill="none" stroke="{S1}" stroke-width="2" stroke-linejoin="round"/>')
    for i, (x, (c, change, v)) in enumerate(zip(xs, prog)):
        body.append(f'<circle cx="{x:.1f}" cy="{y(v):.1f}" r="5" fill="{S1}" stroke="{SURFACE}" stroke-width="2">'
                    f'<title>{escape(c)}: {v:.3f} ({escape(change)})</title></circle>')
        body.append(f'<text transform="translate({x:.1f},{T + ph + 18}) rotate(-35)" font-size="12" '
                    f'fill="{INK2}" text-anchor="end">{escape(c)}</text>')
        prev = prog[i - 1][2] if i else None
        if i in (0, len(prog) - 1) or (prev is not None and v - prev >= 0.05):  # selective direct labels
            body.append(text(x, y(v) - 12, f"{v:.3f}", 12, INK, "middle", "600"))
    gain = prog[-1][2] / prog[0][2]
    flat = next(c for c, _, v in prog if round(v, 3) == round(prog[-1][2], 3))
    body.append(text(L, H - 18, f"{prog[0][2]:.3f} → {prog[-1][2]:.3f}: {gain:.1f}× the starter. Flat from "
                     f"{flat} on: later commits added conflicts() and hardening the public set does not "
                     "exercise.", 12, INK2))
    return svg(W, H, body, "Public score per commit", "Line chart of the public final score for each commit, "
               f"from {prog[0][2]} to {prog[-1][2]}, against the starter baseline {BASELINE}.")


def fig_categories(cats: dict[str, float], commit: str) -> str:
    items = sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))
    W, rowh, L, R, T = 960, 34, 200, 150, 96
    H = T + rowh * len(items) + 56
    pw = W - L - R
    body = heading("Public score by question category",
                   f"Kit scorer, latest run at {commit} (docs/eval/runs.csv). Two categories sit at 0.33.")
    for g in (0, 0.5, 1.0):
        gx = L + pw * g
        body.append(f'<line x1="{gx:.1f}" x2="{gx:.1f}" y1="{T - 6}" y2="{T + rowh * len(items)}" '
                    f'stroke="{GRID}"/>')
        body.append(text(gx, T + rowh * len(items) + 18, f"{g:.1f}", 12, MUTED, "middle"))
    for i, (k, v) in enumerate(items):
        yy = T + i * rowh
        body.append(text(L - 12, yy + 21, k, 13, INK, "end"))
        body.append(bar_h(L, yy + 6, pw * v, rowh - 12, S1, f"{k}: {v:.2f}"))
        body.append(text(L + pw * v + 8, yy + 21, f"{v:.2f}", 13, INK, weight="600"))
        if v < 0.4:
            body.append(text(L + pw * v + 48, yy + 21, "← known limit (phase 2)", 12, INK2))
    return svg(W, H, body, "Public score by category",
               "Horizontal bar chart of per-category public scores: "
               + ", ".join(f"{k} {v:.2f}" for k, v in items) + ".")


def fig_suites(latest: dict[str, dict]) -> str:
    suites = [s for s in SUITE_ORDER if s in latest]
    W, H, L, R, T, B = 960, 460, 72, 40, 110, 70
    pw, ph = W - L - R, H - T - B
    base = T + ph
    gw = pw / len(suites)
    bw = min(64, gw / 3)
    body = heading("Four suites, one scorer",
                   "Final = kit weights (accuracy 55, conflicts 15, consistency 15, speed 15). "
                   "Conflicts use our own gold list.")
    lx = L
    for name, col in (("final score", S1), ("accuracy", S2)):  # legend, one row above the plot
        body.append(f'<rect x="{lx}" y="80" width="12" height="12" rx="2" fill="{col}"/>')
        body.append(text(lx + 18, 91, name, 13, INK2))
        lx += 130
    for g in (0, 0.25, 0.5, 0.75, 1.0):
        gy = base - ph * g
        body.append(f'<line x1="{L}" x2="{W - R}" y1="{gy:.1f}" y2="{gy:.1f}" stroke="{GRID}"/>')
        body.append(text(L - 10, gy + 4, f"{g:.2f}", 12, MUTED, "end"))
    for i, s in enumerate(suites):
        cx = L + gw * i + gw / 2
        for j, (key, col) in enumerate((("final", S1), ("accuracy", S2))):
            v = latest[s][key]
            x = cx - bw - 1 + j * (bw + 2)  # 2px surface gap between adjacent bars
            body.append(bar_v(x, base, bw, ph * v, col, f"{s} {key}: {v:.3f}"))
            body.append(text(x + bw / 2, base - ph * v - 8, f"{v:.3f}", 12, INK, "middle", "600"))
        body.append(text(cx, base + 22, SUITE_LABEL.get(s, s), 13, INK, "middle"))
    body.append(text(L, H - 14, "dev was tuned against after its first run (0.797): treat it as regression, "
                     "not a held-out score. blind first ran at 0.800.", 12, INK2))
    return svg(W, H, body, "Final score and accuracy per suite",
               "Grouped bars: " + "; ".join(f"{s} final {latest[s]['final']:.3f}, accuracy "
                                            f"{latest[s]['accuracy']:.3f}" for s in suites) + ".")


def fig_latency(scorer: dict) -> str:
    suites = [s for s in SUITE_ORDER if s in scorer["suites"]]
    lat = {s: [x * 1000 for x in scorer["suites"][s]["latencies_s"]] for s in suites}
    top = max(max(v) for v in lat.values())
    xmax = max(50.0, (int(top / 25) + 1) * 25.0)
    W, rowh, L, R, T = 960, 64, 200, 60, 104
    H = T + rowh * len(suites) + 70
    pw = W - L - R
    x = lambda ms: L + pw * ms / xmax
    body = heading("Answer latency per question (ms)",
                   f"Every answer() call, run {scorer['commit']} on a shared runner. Full speed marks need "
                   "p95 ≤ 2,000 ms.")
    step = 25 if xmax <= 150 else 50
    for g in range(0, int(xmax) + 1, step):
        body.append(f'<line x1="{x(g):.1f}" x2="{x(g):.1f}" y1="{T - 8}" y2="{T + rowh * len(suites)}" '
                    f'stroke="{GRID}"/>')
        body.append(text(x(g), T + rowh * len(suites) + 18, f"{g}", 12, MUTED, "middle"))
    for i, s in enumerate(suites):
        cy = T + i * rowh + rowh / 2
        xs = lat[s]
        p50, p95 = percentile(xs, 0.5), percentile(xs, 0.95)
        body.append(text(L - 12, cy + 4, SUITE_LABEL.get(s, s), 13, INK, "end"))
        for k, ms in enumerate(xs):  # deterministic jitter so dots don't stack
            jy = cy - 12 + (k * 7 % 25)
            body.append(f'<circle cx="{x(ms):.1f}" cy="{jy:.1f}" r="4" fill="{S1}" fill-opacity="0.75" '
                        f'stroke="{SURFACE}" stroke-width="1"><title>{ms:.1f} ms</title></circle>')
        body.append(f'<line x1="{x(p95):.1f}" x2="{x(p95):.1f}" y1="{cy - 22:.1f}" y2="{cy + 18:.1f}" '
                    f'stroke="{INK}" stroke-width="2"><title>{s} p95 {p95:.0f} ms</title></line>')
        body.append(text(x(p95) + 6, cy - 24, f"p95 {p95:.0f} · p50 {p50:.0f}", 12, INK))
    worst = max(percentile(v, 0.95) for v in lat.values())
    body.append(text(L, H - 18, f"Worst suite p95 is {worst:.0f} ms, about {2000 / max(worst, 1):.0f}× under the "
                     "2 s full-marks line. Ingest is not timed for scoring.", 12, INK2))
    return svg(W, H, body, "Answer latency per question",
               "Dot strip per suite of per-question answer latency in milliseconds with the p95 marked.")


def fig_architecture() -> str:
    W, H = 960, 520
    body = heading("How an answer is made", "Ingest is where history is kept and conflicts are found; "
                   "answer() only reads.")

    def box(x, y, w, h, title, lines, accent=S1):
        out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="#ffffff" stroke="{GRID}" '
               f'stroke-width="1.5"/>', f'<rect x="{x}" y="{y}" width="5" height="{h}" rx="2" fill="{accent}"/>',
               text(x + 16, y + 24, title, 15, INK, weight="600")]
        out += [text(x + 16, y + 46 + 18 * k, ln, 12, INK2) for k, ln in enumerate(lines)]
        return out

    def arrow(x1, y1, x2, y2):
        """Straight horizontal or vertical arrow; the head is a plain polygon so every renderer draws it."""
        dx, dy = (x2 > x1) - (x2 < x1), (y2 > y1) - (y2 < y1)
        bx, by = x2 - 8 * dx, y2 - 8 * dy
        head = f"{x2},{y2} {bx - 4 * dy},{by - 4 * dx} {bx + 4 * dy},{by + 4 * dx}"
        return [f'<line x1="{x1}" y1="{y1}" x2="{bx}" y2="{by}" stroke="{INK2}" stroke-width="1.5"/>',
                f'<polygon points="{head}" fill="{INK2}"/>']

    body += box(32, 100, 190, 150, "1 · Ingest", ["one call per snapshot,", "oldest first", "chunk: table rows,",
                                                    "label: value, sentences", "page kind + authority"])
    body += box(252, 100, 220, 150, "2 · Versioned fact store", ["chunk stored once with a", "bitmask of crawls",
                                                                  "(never overwritten)", "fact key = scope +",
                                                                  "term / tier / rate·APY·APR"])
    body += box(502, 100, 190, 150, "3 · As-of resolution", ["view = newest snapshot", "≤ as_of (or latest)",
                                                              "currency rules pick one", "value per fact key;",
                                                              "losers are shadowed"])
    body += box(722, 100, 206, 150, "4 · Answer", ["intent + BM25 × authority", "× recency × qualifier fit",
                                                    "gates, then one value", "per fact, with sources",
                                                    "and a conflict note"])
    body += box(252, 320, 220, 120, "Conflicts at ingest", ["same key, different value", "→ one entry per fact",
                                                             "stale pages, rate-sheet", "changes, dated claims"], S2)
    body += box(722, 320, 206, 120, "Refusal", ["nothing passes the gates,", "or < 25% of the question",
                                                 "is covered → \"I don't", "know\", no digits"], S2)
    body += arrow(222, 175, 250, 175)
    body += arrow(472, 175, 500, 175)
    body += arrow(692, 175, 720, 175)
    body += arrow(362, 250, 362, 318)
    body += arrow(825, 250, 825, 318)
    body.append(text(370, 290, "conflicts()", 12, MUTED))
    body.append(text(833, 290, "gate fails", 12, MUTED))
    body.append(text(32, 480, "Tie-break for one fact: offer live → newest effective date → in this crawl → "
                     "authority → newest publication → latest-crawl value.", 12, INK2))
    body.append(text(32, 500, "Qualifiers (term, tier, rate vs APY vs APR, personal vs business, model year) are "
                     "different facts, never conflicts.", 12, INK2))
    return svg(W, H, body, "Architecture and flow",
               "Ingest, versioned fact store, as-of resolution, answer; conflicts are found at ingest and "
               "answers refuse when no passage passes the gates.")


def build_all() -> dict[str, str]:
    prog = score_progression(RESULTS.read_text())
    with RUNS.open(newline="") as f:
        latest = latest_runs(list(csv.DictReader(f)))
    scorer = json.loads(SCORER.read_text())
    return {
        "01-score-per-run.svg": fig_score_per_run(prog),
        "02-public-by-category.svg": fig_categories(latest["public"]["by_category"], latest["public"]["commit"]),
        "03-suites.svg": fig_suites(latest),
        "04-latency.svg": fig_latency(scorer),
        "05-architecture.svg": fig_architecture(),
    }


def main() -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    for name, doc in build_all().items():
        (FIG / name).write_text(doc)
        print(f"wrote {FIG / name}")


if __name__ == "__main__":
    main()
