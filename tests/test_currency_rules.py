"""Unit tests for the currency rules in NOTES.md (D3). Synthetic pages only: no snapshot data needed.

Run:  python -m unittest discover -s tests
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "submission"))

from contract import Answer, Page, Snapshot  # noqa: E402

import solution  # noqa: E402
from bankgpt.chunker import page_kind  # noqa: E402
from bankgpt.text import amount_range, as_of_from_text, numwords_to_digits, offer_end, year_range  # noqa: E402

CU = "https://www.dupaco.com"


def snap(sid: str, taken: str, pages: dict[str, str]) -> Snapshot:
    return Snapshot(sid, taken, [Page(url=CU + path, title=path.strip("/") or "home", markdown=md,
                                      fetched_at=taken + "T00:00:00Z") for path, md in pages.items()])


RATE_TABLE = """# Rates

Certificates

| Certificate | Dividend Rate | Annual Percentage Yield (APY) | Min. to Earn |
| --- | --- | --- | --- |
| 12 Month | {d12} | {a12} | $1,000.00 |
| 6 Month | 3.37% | 3.40% | $1,000.00 |

Rates are effective as of {eff} and are subject to change without notice.

Auto Loans

| Loan Program | Term | Annual Percentage Rate (As Low As) |
| --- | --- | --- |
| 2024-27 Model Year | 60 Months | 5.39% APR |
| 2017-20 Model Year | 60 Months | 6.34% APR |

Rates are effective as of 08/31/2026.
"""

CALC = """# Vehicle affordability calculator

Auto Loan Rates

| Loan Program | Term | Annual Percentage Rate (As Low As) |
| --- | --- | --- |
| 2023-26 Model Year | 60 Months | 5.34% APR |
| 2016-19 Model Year | 60 Months | 6.29% APR |

Rates are effective as of 1/1/2026.
"""

FEES = """# Fees

Lost debit card replacement fee: $2.50
"""

PROMO = """# Promo

Summer special: 6-month certificate APY 2.95% (offer ended 2025-09-30)
"""

SAVINGS = """# Rates

Share Savings

| Account Type | Balance Tier | Dividend Rate | Annual Percentage Yield (APY) |
| --- | --- | --- | --- |
| Share Savings | $25,000+ | 0.20% | 0.20% |
| Share Savings | $10,000 – $24,999.99 | 0.15% | 0.15% |
| Share Savings | $0 – $9,999.99 | 0.10% | 0.10% |

Rates are effective as of 06/02/2026.
"""


def build_ks():
    ks = solution.build()
    ks.ingest(snap("01_2026-05-12_baseline", "2026-05-12", {
        "/rates": RATE_TABLE.format(d12="2.88%", a12="2.90%", eff="03/03/2026"),
        "/fees": FEES,
        "/calculators/vehicle-affordability": CALC,
    }))
    ks.ingest(snap("02_2026-09-28_latest", "2026-09-28", {
        "/rates": RATE_TABLE.format(d12="3.37%", a12="3.40%", eff="06/02/2026"),
        "/calculators/vehicle-affordability": CALC,
        "/promo": PROMO,
        "/business-rates": SAVINGS,
    }))
    return ks


class TextHelpers(unittest.TestCase):
    def test_offer_end_formats(self):
        self.assertEqual(offer_end("offer ended 2025-09-30"), "2025-09-30")
        self.assertEqual(offer_end("Offer valid through December 31, 2025."), "2025-12-31")
        self.assertEqual(offer_end("expires 3/31/26"), "2026-03-31")
        self.assertIsNone(offer_end("no date here"))

    def test_ranges(self):
        self.assertEqual(amount_range("$10,000 – $24,999.99"), (10000.0, 24999.99))
        self.assertEqual(amount_range("$10,000-24,999"), (10000.0, 24999.0))
        self.assertEqual(amount_range("$25,000+")[0], 25000.0)
        self.assertEqual(amount_range("$100,000 or less"), (0.0, 100000.0))
        self.assertEqual(year_range("2024-27 Model Year"), (2024, 2027))
        self.assertEqual(year_range("2016 Model Year (and older)"), (1900, 2016))

    def test_dates_and_numwords(self):
        self.assertEqual(as_of_from_text("What was it as of May 2026?"), "2026-05-31")
        self.assertEqual(numwords_to_digits("a six month certificate"), "a 6 month certificate")


class AuthorityRanking(unittest.TestCase):
    def test_order(self):
        order = ["/rates", "/personal/loans/auto-loans", "/", "/checking-offer-lp", "/newsroom/x",
                 "/learn/blog/y", "/business/resources/guide", "/calculators/vehicle-affordability",
                 "/newsroom/tag/news", "/offer-expired", "/privacy-policy?campaignId=1"]
        auth = [page_kind(CU + p)[1] for p in order]
        self.assertEqual(auth, sorted(auth, reverse=True), dict(zip(order, auth)))
        self.assertEqual(len(set(auth)), len(auth), dict(zip(order, auth)))
        self.assertEqual(page_kind(CU + "/hubfs/fee-schedule.pdf")[1], page_kind(CU + "/newsroom/x")[1])


class CurrencyRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ks = build_ks()

    def ask(self, q, as_of=None) -> Answer:
        a = self.ks.answer(q, as_of=as_of)
        self.assertIsInstance(a, Answer)
        return a

    def test_latest_snapshot_by_default(self):
        a = self.ask("What is the 12-month certificate APY?")
        self.assertIn("3.40%", a.text)
        self.assertNotIn("2.90%", a.text)

    def test_as_of_answers_from_older_snapshot(self):
        a = self.ask("What was the 12-month certificate APY?", as_of="2026-06-01")
        self.assertIn("2.90%", a.text)
        self.assertNotIn("3.40%", a.text)

    def test_rate_sheet_beats_stale_calculator(self):
        a = self.ask("What's the APR for a 60-month auto loan on a 2025 model-year vehicle?")
        self.assertIn("5.39%", a.text)
        self.assertNotIn("5.34%", a.text)
        self.assertTrue(any(s.url.endswith("/rates") for s in a.sources))

    def test_model_year_tier(self):
        a = self.ask("60-month auto loan APR for a 2018 car?")
        self.assertIn("6.34%", a.text)
        self.assertNotIn("5.39%", a.text)

    def test_balance_tier(self):
        a = self.ask("What dividend rate does Share Savings pay on $12,000?")
        self.assertIn("0.15%", a.text)
        self.assertNotIn("0.20%", a.text)

    def test_expired_offer_never_wins(self):
        a = self.ask("What is the 6-month certificate APY?")
        self.assertIn("3.40%", a.text)
        self.assertNotIn("2.95%", a.text)

    def test_page_missing_from_latest_crawl_is_carried_forward(self):
        a = self.ask("How much is a lost debit card replacement?")
        self.assertIn("$2.50", a.text)

    def test_honest_refusal_has_no_numbers(self):
        a = self.ask("What is the rate on a 7/1 adjustable-rate mortgage?")
        self.assertFalse(a.confident)
        self.assertFalse(re.search(r"\d", a.text), a.text)

    def test_unlisted_term_is_refused(self):
        a = self.ask("What APY does the 9-month certificate pay?")
        self.assertFalse(a.confident)

    def test_repeat_is_identical(self):
        q = "What is the 12-month certificate APY?"
        self.assertEqual(self.ask(q).text, self.ask(q).text)

    def test_conflicts_resolve_to_current_value(self):
        cs = self.ks.conflicts()
        auto = [c for c in cs if "5.34%" in " ".join(c.values)]
        self.assertTrue(auto, [c.topic for c in cs])
        self.assertIn("5.39%", auto[0].resolution)
        self.assertTrue(auto[0].reason)
        cert = [c for c in cs if "2.9% APY" in " ".join(c.values)]
        self.assertTrue(cert, [c.values for c in cs])
        self.assertIn("3.4%", cert[0].resolution)
        # tiers of one table are different facts, never a conflict
        self.assertFalse([c for c in cs if "0.15%" in " ".join(c.values) and "0.2%" in " ".join(c.values)])


class KitSelftest(unittest.TestCase):
    """The kit's own synthetic selftest, scored with the kit's scorer."""

    def test_selftest_scores_full_marks(self):
        from run_eval import load_snapshots
        from scoring import score_conflicts, score_item
        import json
        st = ROOT / "harness" / "selftest"
        ks = solution.build()
        for s in load_snapshots(st / "snapshots"):
            ks.ingest(s)
        for line in (st / "eval.jsonl").read_text().splitlines():
            item = json.loads(line)
            r = score_item(item, ks.answer(item["question"], item.get("as_of")))
            self.assertGreaterEqual(r["correctness"], 1.0, (item["id"], r))
        rep = score_conflicts(json.loads((st / "conflicts_gold.json").read_text()), ks.conflicts())
        self.assertEqual(rep["matched"], 1, rep)
        self.assertEqual(rep["resolution_accuracy"], 1.0, rep)


if __name__ == "__main__":
    unittest.main()
