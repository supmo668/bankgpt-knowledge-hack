#!/usr/bin/env python3
"""Run every eval suite with the kit's own scorer and write the proof-of-work tables.

    python scripts/eval_report.py              # writes docs/eval/latest.md, appends docs/eval/runs.csv

Suites: the public practice set, our dev set (as-of / reworded / refusal items built from snapshot diffs),
our blind set (written before the system was run on it), and the kit's synthetic selftest. Conflicts are
scored against our own gold list (docs/eval/conflicts_gold.json) because the public kit ships none.
Only scores are written: no answer text, so no site content lands in the repo.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "submission"))

from run_eval import load_snapshots, to_answer  # noqa: E402
from scoring import REFUSAL_RE, score_conflicts, score_item, summarize  # noqa: E402

import solution  # noqa: E402

SUITES = [
    ("public", ROOT / "eval/public_eval.jsonl", ROOT / "snapshots", ROOT / "docs/eval/conflicts_gold.json"),
    ("dev", ROOT / "docs/eval/dev_eval.jsonl", ROOT / "snapshots", ROOT / "docs/eval/conflicts_gold.json"),
    ("blind", ROOT / "docs/eval/dev2_blind.jsonl", ROOT / "snapshots", ROOT / "docs/eval/conflicts_gold.json"),
    ("selftest", ROOT / "harness/selftest/eval.jsonl", ROOT / "harness/selftest/snapshots",
     ROOT / "harness/selftest/conflicts_gold.json"),
]


def diagnostics(items: list[dict], answers: list, results: list[dict]) -> dict:
    """Two refusal diagnostics (no threshold; they are not part of the score).

    "Refused" uses the scorer's own test: confident=False, or refusal wording in the text.

    unsupported_confident = confident answers the evidence does not support / all confident answers on gradable
      items. A confident answer is unsupported when it hits none of `must_include` or hits a `must_not_include`
      trap (scorer correctness 0), or when the item is `should_refuse`. Items that are neither `should_refuse`
      nor carry `must_include` cannot be graded and are left out of both counts. Undefined (None) when there
      are no confident answers.
    wrongly_refused = refusals / all answerable items (every item that is not `should_refuse`).
    """
    uc_num = uc_den = wr_num = wr_den = 0
    for it, ans, r in zip(items, answers, results):
        refused = (not ans.confident) or bool(REFUSAL_RE.search(ans.text or ""))
        should_refuse = it.get("category") == "should_refuse"
        if not should_refuse:
            wr_den += 1
            wr_num += refused
        if refused or not (should_refuse or it.get("must_include")):
            continue
        uc_den += 1
        uc_num += should_refuse or r["correctness"] == 0
    return {"unsupported_confident": {"num": uc_num, "den": uc_den,
                                      "rate": round(uc_num / uc_den, 4) if uc_den else None},
            "wrongly_refused": {"num": wr_num, "den": wr_den, "rate": round(wr_num / wr_den, 4) if wr_den else None}}


def fmt_rate(d: dict) -> str:
    return f"{d['rate']:.3f} ({d['num']}/{d['den']})" if d["rate"] is not None else f"undefined ({d['num']}/{d['den']})"


def run_suite(ks, eval_path: Path, gold_path: Path, repeat_sample: int = 20) -> dict:
    items = [json.loads(x) for x in eval_path.read_text().splitlines() if x.strip()]
    results, answers = [], []
    for it in items:
        t = time.time()
        ans = to_answer(ks.answer(it["question"], it.get("as_of")))
        r = score_item(it, ans)
        r.update(latency_s=round(time.time() - t, 3), error=None, answer={"text": ans.text})
        results.append(r)
        answers.append(ans)
    repeats = [{"id": it["id"], "text": to_answer(ks.answer(it["question"], it.get("as_of"))).text}
               for it in items[:: max(1, len(items) // repeat_sample)]]
    conf = score_conflicts(json.loads(gold_path.read_text()), ks.conflicts()) if gold_path.exists() else None
    rep = summarize(results, conf, repeats)
    rep["n_conflicts_submitted"] = len(ks.conflicts())
    rep["diagnostics"] = diagnostics(items, answers, results)
    return rep


def git_rev() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return "unknown"


def main() -> None:
    reports, cache = {}, {}
    for name, ev, snaps, gold in SUITES:
        if not any(snaps.glob("*/manifest.json")) and not (snaps.parent / "snapshots_zip").exists():
            print(f"skip {name}: no snapshots under {snaps}")
            continue
        if snaps not in cache:
            ks = solution.build()
            t = time.time()
            for s in load_snapshots(snaps):
                ks.ingest(s)
            cache[snaps] = (ks, round(time.time() - t, 1))
        ks, ingest_s = cache[snaps]
        rep = run_suite(ks, ev, gold)
        rep["ingest_seconds"] = ingest_s
        reports[name] = rep
        print(f"{name:9s} final={rep['final_score']:.4f} qa={rep['qa_score']:.4f}")

    rev, now = git_rev(), datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = ROOT / "docs/eval"
    out.mkdir(parents=True, exist_ok=True)
    lines = [f"# Latest eval run\n\nCommit `{rev}`, {now}. Generated by `scripts/eval_report.py`; scorer = the kit's "
             "`harness/scoring.py`. Conflicts are scored against our own gold list (the public kit has none).\n",
             "| Suite | Items | Final | Accuracy | Consistency | p95 answer (s) | Conflicts F1 | Conflict resolution "
             "| Stale-trap hits | Ingest (s) |", "|---|---|---|---|---|---|---|---|---|---|"]
    for name, r in reports.items():
        c = r.get("conflicts") or {}
        lines.append(f"| {name} | {r['questions']} | **{r['final_score']:.3f}** | {r['qa_score']:.3f} | "
                     f"{r['consistency']['score']} | {r['speed']['p95_s']} | {c.get('f1', '-')} | "
                     f"{c.get('resolution_accuracy', '-')} | {r['stale_trap_hits']} | {r['ingest_seconds']} |")
    lines += ["\n## Diagnostics (no threshold, not part of the score)\n",
              "Unsupported-confident rate = confident answers that hit no required fact, hit a trap, or should have "
              "been refused / confident answers on gradable items; undefined when there are none. "
              "Wrongly-refused rate = refusals / answerable (non-`should_refuse`) items.\n",
              "| Suite | Unsupported-confident rate | Wrongly-refused rate (answerable) |", "|---|---|---|"]
    for name, r in reports.items():
        d = r["diagnostics"]
        lines.append(f"| {name} | {fmt_rate(d['unsupported_confident'])} | {fmt_rate(d['wrongly_refused'])} |")
    for name, r in reports.items():
        lines += [f"\n## {name}: per category\n", "| Category | n | Score |", "|---|---|---|"]
        lines += [f"| {k} | {v['n']} | {v['score']:.2f} |" for k, v in r["by_category"].items()]
        c = r.get("conflicts")
        if c:
            lines.append(f"\nConflicts: submitted {c['submitted']}, gold {c['gold']}, matched {c['matched']}, "
                         f"recall {c['recall']}, precision {c['precision']}.")
    (out / "latest.md").write_text("\n".join(lines) + "\n")

    runs = out / "runs.csv"
    new = not runs.exists()
    with runs.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["utc", "commit", "suite", "final", "accuracy", "conflicts_f1", "p95_s", "by_category"])
        for name, r in reports.items():
            w.writerow([now, rev, name, r["final_score"], r["qa_score"], (r.get("conflicts") or {}).get("f1"),
                        r["speed"]["p95_s"], json.dumps({k: v["score"] for k, v in r["by_category"].items()})])
    print(f"wrote {out / 'latest.md'} and appended {runs}")


if __name__ == "__main__":
    main()
