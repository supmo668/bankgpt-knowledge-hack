#!/usr/bin/env python3
"""Re-run every eval suite with the kit's scorer and keep only numbers for the deck figures.

    python scripts/deck_measure.py [--snapshots DIR]     # writes docs/deck/data/scorer_output.json

Same suites and scorer as scripts/eval_report.py. The output holds scores, pillars and per-question
latencies, never question, answer or page text, so it is safe to commit to this public repo.
"""
from __future__ import annotations

import argparse
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
from scoring import score_conflicts, score_item, summarize  # noqa: E402

import solution  # noqa: E402

OUT = ROOT / "docs/deck/data/scorer_output.json"


def suites(snapshots: Path) -> list[tuple[str, Path, Path, Path]]:
    gold = ROOT / "docs/eval/conflicts_gold.json"
    return [
        ("public", ROOT / "eval/public_eval.jsonl", snapshots, gold),
        ("dev", ROOT / "docs/eval/dev_eval.jsonl", snapshots, gold),
        ("blind", ROOT / "docs/eval/dev2_blind.jsonl", snapshots, gold),
        ("selftest", ROOT / "harness/selftest/eval.jsonl", ROOT / "harness/selftest/snapshots",
         ROOT / "harness/selftest/conflicts_gold.json"),
    ]


def run_suite(ks, eval_path: Path, gold_path: Path, repeat_sample: int = 20) -> dict:
    items = [json.loads(x) for x in eval_path.read_text().splitlines() if x.strip()]
    results = []
    for it in items:
        t = time.perf_counter()
        ans = to_answer(ks.answer(it["question"], it.get("as_of")))
        r = score_item(it, ans)
        r.update(latency_s=round(time.perf_counter() - t, 4), error=None, answer={"text": ans.text})
        results.append(r)
    repeats = [{"id": it["id"], "text": to_answer(ks.answer(it["question"], it.get("as_of"))).text}
               for it in items[:: max(1, len(items) // repeat_sample)]]
    conf = score_conflicts(json.loads(gold_path.read_text()), ks.conflicts()) if gold_path.exists() else None
    rep = summarize(results, conf, repeats)
    c = rep.get("conflicts") or {}
    return {
        "questions": rep["questions"],
        "final": rep["final_score"],
        "accuracy": rep["qa_score"],
        "pillars": rep["pillars"],
        "speed": rep["speed"],
        "stale_trap_hits": rep["stale_trap_hits"],
        "conflicts": {k: c.get(k) for k in ("submitted", "gold", "matched", "recall", "precision", "f1",
                                            "resolution_accuracy")} if c else None,
        "by_category": {k: v["score"] for k, v in rep["by_category"].items()},
        # Numbers only: one latency per question, sorted, so no item can be traced back to its text.
        "latencies_s": sorted(r["latency_s"] for r in results),
    }


def git_rev() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return "unknown"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshots", type=Path, default=ROOT / "snapshots",
                    help="unpacked kit snapshots (gitignored; never committed)")
    args = ap.parse_args(argv)
    out, cache = {}, {}
    for name, ev, snaps, gold in suites(args.snapshots.resolve()):
        if not any(snaps.glob("*/manifest.json")):
            print(f"skip {name}: no snapshots under {snaps}")
            continue
        if snaps not in cache:
            ks = solution.build()
            t = time.perf_counter()
            for s in load_snapshots(snaps):
                ks.ingest(s)
            cache[snaps] = (ks, round(time.perf_counter() - t, 1))
        ks, ingest_s = cache[snaps]
        out[name] = run_suite(ks, ev, gold) | {"ingest_s": ingest_s}
        print(f"{name:9s} final={out[name]['final']:.4f} acc={out[name]['accuracy']:.4f} "
              f"p95={out[name]['speed']['p95_s']}s")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"commit": git_rev(),
                               "utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                               "suites": out}, indent=1) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
