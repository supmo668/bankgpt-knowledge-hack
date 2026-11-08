"""Bug-exposing tests from the HACA-5 iteration-1 quality gate (one class per finding)."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "submission"))

from contract import Page, Snapshot  # noqa: E402

import solution  # noqa: E402
from bankgpt.chunker import chunk_page  # noqa: E402

CU = "https://www.dupaco.com"


def snap(sid, taken, pages):
    return Snapshot(sid, taken, [Page(url=CU + p, title=p.strip("/"), markdown=md, fetched_at=taken)
                                 for p, md in pages.items()])


def cert_table(caption, rows, eff):
    body = "\n".join(f"| {t} | {apy} | $1,000.00 |" for t, apy in rows)
    return (f"{caption}\n\n| Certificate | Annual Percentage Yield (APY) | Min. to Earn |\n| --- | --- | --- |\n"
            f"{body}\n\nRates are effective as of {eff} and are subject to change.\n")


class C1_EffectiveDateRefreshedOnUnchangedRow(unittest.TestCase):
    """The rate sheet row text is unchanged but its table's effective date moves forward."""

    def test_rate_sheet_with_moved_date_still_wins(self):
        ks = solution.build()
        ks.ingest(snap("01", "2026-01-05", {"/rates": "# Rates\n\n" + cert_table("Regular", [("12 Month", "3.05%")], "01/01/2026")}))
        ks.ingest(snap("02", "2026-06-05", {
            "/rates": "# Rates\n\n" + cert_table("Regular", [("12 Month", "3.05%")], "06/01/2026"),
            "/certificates": "# Certificates\n\n" + cert_table("Regular", [("12 Month", "2.55%")], "03/01/2026"),
        }))
        a = ks.answer("What is the 12 month certificate APY?")
        self.assertIn("3.05%", a.text)
        self.assertNotIn("2.55%", a.text)


class C4_NewTopModelYearTier(unittest.TestCase):
    def test_new_tier_not_shadowed(self):
        def auto(rows):
            body = "\n".join(f"| {y} Model Year | 60 Months | {r} APR |" for y, r in rows)
            return ("# Rates\n\nAuto Loans\n\n| Loan Program | Term | Annual Percentage Rate (As Low As) |\n"
                    f"| --- | --- | --- |\n{body}\n\nRates are effective as of 08/31/2026.\n")
        ks = solution.build()
        ks.ingest(snap("01", "2026-01-05", {"/rates": auto([("2020-23", "5.39%"), ("2016-19", "6.34%")])}))
        ks.ingest(snap("02", "2026-06-05", {"/rates": auto([("2024-27", "4.99%"), ("2020-23", "5.39%"), ("2016-19", "6.34%")])}))
        a = ks.answer("60-month auto loan APR for a 2025 model year car?")
        self.assertIn("4.99%", a.text)
        self.assertNotIn("5.39%", a.text)


class C3_TableInsertedAbove(unittest.TestCase):
    def test_superlative_stays_in_its_table(self):
        reg = cert_table("Regular Certificates", [("12 Month", "4.00%"), ("24 Month", "3.75%")], "01/01/2026")
        reg2 = cert_table("Regular Certificates", [("12 Month", "4.00%"), ("24 Month", "3.90%")], "06/01/2026")
        promo = cert_table("Promo Specials", [("9 Month Promo", "5.00%")], "06/01/2026")
        ks = solution.build()
        ks.ingest(snap("01", "2026-01-05", {"/rates": "# Rates\n\n" + reg}))
        ks.ingest(snap("02", "2026-06-05", {"/rates": "# Rates\n\n" + promo + "\n" + reg2}))
        a = ks.answer("What is the highest regular certificate APY?")
        self.assertIn("4.00%", a.text)
        self.assertNotIn("5.00%", a.text)


class C5_TieGoesToNewestValue(unittest.TestCase):
    def test_updated_page_wins_equal_claim(self):
        page = lambda apy: "# Certificates\n\n" + cert_table("Regular", [("12 Month", apy)], "01/01/2026")
        ks = solution.build()
        ks.ingest(snap("01", "2026-01-05", {"/certificates": page("3.05%"), "/savings-guide": page("3.05%")}))
        ks.ingest(snap("02", "2026-06-05", {"/certificates": page("3.55%"), "/savings-guide": page("3.05%")}))
        a = ks.answer("What is the 12 month certificate APY?")
        self.assertIn("3.55%", a.text)
        self.assertNotIn("3.05%", a.text)


class C10_ModelYearOutsideEveryTier(unittest.TestCase):
    def test_refused(self):
        md = ("# Rates\n\nAuto Loans\n\n| Loan Program | Term | Annual Percentage Rate (As Low As) |\n| --- | --- | --- |\n"
              "| 2024-27 Model Year | 60 Months | 5.39% APR |\n| 2017-20 Model Year | 60 Months | 6.34% APR |\n\n"
              "Rates are effective as of 08/31/2026.\n")
        ks = solution.build()
        ks.ingest(snap("01", "2026-09-01", {"/rates": md}))
        a = ks.answer("60-month auto loan APR for a 2010 car?")
        self.assertFalse(a.confident, a.text)
        self.assertFalse(re.search(r"\d", a.text))


class C9_CaptionDoesNotLeakAcrossTables(unittest.TestCase):
    def test_second_table_context(self):
        md = ("# Rates\n\nJumbo Certificates\n\n| Certificate | APY |\n| --- | --- |\n| 12 Month Jumbo | 4.50% |\n\n"
              "Regular Certificates\n\n| Certificate | APY |\n| --- | --- |\n| 12 Month | 3.40% |\n")
        rows = [c for c in chunk_page(CU + "/rates", "Rates", md) if c.kind == "row"]
        reg = [c for c in rows if c.text.startswith("12 Month:")][0]
        self.assertNotIn("Jumbo", reg.ctx)


class C8_ConflictDedupeKeepsDistinctFacts(unittest.TestCase):
    def test_two_terms_same_movement(self):
        ks = solution.build()
        rows = lambda v: [("12 Month", v), ("24 Month", v)]
        ks.ingest(snap("01", "2026-01-05", {"/rates": "# Rates\n\n" + cert_table("Regular", rows("2.90%"), "01/01/2026")}))
        ks.ingest(snap("02", "2026-06-05", {"/rates": "# Rates\n\n" + cert_table("Regular", rows("3.40%"), "06/01/2026")}))
        topics = [c.topic for c in ks.conflicts()]
        self.assertTrue(any("12" in t for t in topics) and any("24" in t for t in topics), topics)
        for c in ks.conflicts():
            self.assertGreaterEqual(len(c.values), 2)
            self.assertIn(c.resolution, c.values)
            self.assertTrue(c.reason and c.topic and len(c.sources) >= 2)


class C6_CacheKeyRespectsCase(unittest.TestCase):
    def test_case_variant_does_not_reuse_answer(self):
        ks = solution.build()
        ks.ingest(snap("01", "2026-01-05", {"/rates": "# Rates\n\n" + cert_table("Regular", [("12 Month", "3.05%")], "01/01/2026")}))
        lower = ks.answer("what is the 12 month certificate apy at the elm street branch?")
        upper = ks.answer("What is the 12 month certificate APY at the Elm Street branch?")
        self.assertFalse(upper.confident, upper.text)   # named place the site never mentions
        self.assertIsNotNone(lower)


class C7_AnswerBeforeIngest(unittest.TestCase):
    def test_no_crash(self):
        ks = solution.build()
        a = ks.answer("What is the rate?")
        self.assertFalse(a.confident)
        self.assertEqual(ks.conflicts(), [])


class CacheAfterIngest(unittest.TestCase):
    def test_new_snapshot_changes_answer(self):
        ks = solution.build()
        ks.ingest(snap("01", "2026-01-05", {"/rates": "# Rates\n\n" + cert_table("Regular", [("12 Month", "2.90%")], "01/01/2026")}))
        self.assertIn("2.90%", ks.answer("What is the 12 month certificate APY?").text)
        ks.ingest(snap("02", "2026-06-05", {"/rates": "# Rates\n\n" + cert_table("Regular", [("12 Month", "3.40%")], "06/01/2026")}))
        a = ks.answer("What is the 12 month certificate APY?")
        self.assertIn("3.40%", a.text)
        self.assertEqual(a.sources[0].snapshot_id, "02")
        self.assertIn("2.90%", ks.answer("What is the 12 month certificate APY?", as_of="2026-03-01").text)


if __name__ == "__main__":
    unittest.main()
