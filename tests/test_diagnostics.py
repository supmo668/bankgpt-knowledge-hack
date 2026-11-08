"""The two refusal diagnostics in scripts/eval_report.py (synthetic items; no snapshot data).

Run:  python -m unittest discover -s tests
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "submission"))

from contract import Answer  # noqa: E402
from scoring import score_item  # noqa: E402

_spec = importlib.util.spec_from_file_location("eval_report", ROOT / "scripts/eval_report.py")
eval_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval_report)


def item(i, cat="current_value", must=("3.50%",), must_not=()):
    return {"id": f"i{i}", "question": "What is the 6-month APY?", "category": cat,
            "must_include": list(must), "must_not_include": list(must_not)}


REFUSAL = Answer(text="I don't know: that isn't listed on the website.", confident=False)


def run(pairs):
    items, answers = zip(*pairs)
    results = [score_item(it, a) for it, a in pairs]
    return eval_report.diagnostics(list(items), list(answers), results)


class Diagnostics(unittest.TestCase):
    def test_counts(self):
        d = run([
            (item(1), Answer(text="3.50% APY")),                                  # supported
            (item(2), Answer(text="2.95% APY")),                                  # unsupported: no required fact
            (item(3, must_not=("2.95%",)), Answer(text="3.50% or 2.95%")),        # unsupported: trap hit
            (item(4, cat="should_refuse", must=()), Answer(text="It is 4.00%")),  # unsupported: should refuse
            (item(5, cat="should_refuse", must=()), REFUSAL),                     # correct refusal
            (item(6), REFUSAL),                                                    # wrongly refused
            (item(7), Answer(text="Please contact us.", confident=True)),          # refusal wording = refused
            (item(8, must=()), Answer(text="Something")),                         # ungradable: excluded
        ])
        self.assertEqual(d["unsupported_confident"], {"num": 3, "den": 4, "rate": 0.75})
        self.assertEqual(d["wrongly_refused"], {"num": 2, "den": 6, "rate": 0.3333})

    def test_partial_answer_is_supported(self):
        d = run([(item(1, must=("3.50%", "12-month")), Answer(text="3.50% APY"))])
        self.assertEqual(d["unsupported_confident"]["num"], 0)

    def test_undefined_without_confident_answers(self):
        d = run([(item(1), REFUSAL), (item(2, cat="should_refuse", must=()), REFUSAL)])
        self.assertEqual(d["unsupported_confident"], {"num": 0, "den": 0, "rate": None})
        self.assertEqual(eval_report.fmt_rate(d["unsupported_confident"]), "undefined (0/0)")
        self.assertEqual(d["wrongly_refused"]["rate"], 1.0)

    def test_defined_rate_format(self):   # T-10
        self.assertEqual(eval_report.fmt_rate({"num": 3, "den": 4, "rate": 0.75}), "0.750 (3/4)")

    def test_run_suite_reports_diagnostics(self):   # T-10: wired into every suite report
        import json
        import tempfile

        class Stub:
            def answer(self, q, as_of=None):
                return REFUSAL if "refuse" in q else Answer(text="3.50% APY")

            def conflicts(self):
                return []

        items = [dict(item(1), question="What is the APY?"), dict(item(2), question="please refuse this"),
                 dict(item(3, cat="should_refuse", must=()), question="refuse me")]
        with tempfile.TemporaryDirectory() as tmp:
            ev = Path(tmp) / "eval.jsonl"
            ev.write_text("\n".join(json.dumps(i) for i in items))
            rep = eval_report.run_suite(Stub(), ev, Path(tmp) / "no_gold.json")
        self.assertEqual(rep["diagnostics"]["wrongly_refused"], {"num": 1, "den": 2, "rate": 0.5})
        self.assertEqual(rep["diagnostics"]["unsupported_confident"], {"num": 0, "den": 1, "rate": 0.0})

    def test_undefined_without_answerable_items(self):
        d = run([(item(1, cat="should_refuse", must=()), REFUSAL)])
        self.assertIsNone(d["wrongly_refused"]["rate"])


if __name__ == "__main__":
    unittest.main()
