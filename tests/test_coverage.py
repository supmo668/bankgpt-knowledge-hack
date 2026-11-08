"""Coverage tests added at the iteration-1 quality gate: chunker units, expiry isolated from authority,
as_of edges, refusal contract, scope (business vs personal) is not a conflict."""
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


def rows(md, url="/x"):
    return [c for c in chunk_page(CU + url, "t", md) if c.kind == "row"]


class Chunker(unittest.TestCase):
    def test_headerless_pipe_table_and_subcaption(self):
        md = "# Fees\n\n| Fees |\n| Wire transfer | $25.00 |\n| Stop payment | $30.00 |\n"
        r = rows(md)
        self.assertEqual([c.cells["Item"] for c in r], ["Wire transfer", "Stop payment"])
        self.assertEqual(r[0].cells["Fee"], "$25.00")
        self.assertTrue(r[0].ctx.endswith("Fees"))

    def test_data_header_row_is_kept(self):
        md = "|Official check|$5.00 each|\n|---|---|\n|Domestic wire|$25.00 each|\n"
        items = sorted(c.cells["Item"] for c in rows(md))
        self.assertEqual(items, ["Domestic wire", "Official check"])

    def test_label_value_lines(self):
        md = "# Info\n\nRouting Number: 273974581\n\n6-month certificate APY: 3.50%\n\nLost card fee: $2.50 (per card).\n"
        vals = {c.cells["Item"]: c.cells["Value"] for c in rows(md)}
        self.assertEqual(vals, {"Routing Number": "273974581", "6-month certificate APY": "3.50%",
                                "Lost card fee": "$2.50"})

    def test_effective_date_attaches_to_its_table_only(self):
        md = ("# Rates\n\nA\n\n| Term | APY |\n|:---|---:|\n| 12 Month | 3.40% |\n\nRates are effective as of 06/02/2026.\n\n"
              "B\n\n| Term | APY |\n| --- | --- |\n| 24 Month | 2.50% |\n")
        r = {c.text.split(":")[0]: c for c in rows(md)}
        self.assertEqual(r["12 Month"].effective, "06/02/2026")
        self.assertIsNone(r["24 Month"].effective)

    def test_model_year_tiers_are_ranked(self):
        md = ("| Program | Term | APR |\n| --- | --- | --- |\n| 2024-27 Model Year | 60 Months | 5.39% |\n"
              "| 2017-20 Model Year | 60 Months | 6.34% |\n")
        aliases = sorted(v for c in rows(md) for v in c.key_alias.values())
        self.assertEqual(aliases, ["model-year tier 0", "model-year tier 1"])


class ExpiryWithoutAHigherAuthorityPage(unittest.TestCase):
    PROMO = "# Promo\n\nSummer special: 9-month certificate APY: 4.95% (offer ends {end})\n"

    def test_expired_promo_alone_is_refused(self):
        ks = solution.build()
        ks.ingest(snap("01", "2026-09-28", {"/promo-lp": self.PROMO.format(end="2025-09-30")}))
        a = ks.answer("What does the 9-month certificate pay?")
        self.assertFalse(a.confident, a.text)
        self.assertNotIn("4.95", a.text)

    def test_offer_ending_today_is_still_live(self):
        ks = solution.build()
        ks.ingest(snap("01", "2026-10-28", {"/promo-lp": self.PROMO.format(end="2026-10-28")}))
        self.assertIn("4.95%", ks.answer("What APY does the 9-month certificate pay?").text)


class AsOfEdges(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        t = "# Rates\n\nRegular\n\n| Certificate | APY |\n| --- | --- |\n| 12 Month | {v} |\n\nRates are effective as of {e}.\n"
        cls.ks = solution.build()
        cls.ks.ingest(snap("01", "2026-05-12", {"/rates": t.format(v="2.90%", e="05/01/2026")}))
        cls.ks.ingest(snap("02", "2026-09-28", {"/rates": t.format(v="3.40%", e="09/01/2026")}))

    def sid(self, as_of, q="What is the 12 month certificate APY?"):
        return self.ks.answer(q, as_of=as_of).sources[0].snapshot_id

    def test_edges(self):
        self.assertEqual(self.sid("2026-01-01"), "01")            # before the first snapshot -> oldest
        self.assertEqual(self.sid("2026-05-12"), "01")            # exact date is inclusive
        self.assertEqual(self.sid("2026-09-27"), "01")            # the day before the next snapshot
        self.assertEqual(self.sid("2026-09-28"), "02")
        self.assertEqual(self.sid("2026-05-12T10:00:00Z"), "01")  # timestamps are accepted
        self.assertEqual(self.sid(None), "02")

    def test_date_in_question_text(self):
        a = self.ks.answer("What was the 12 month certificate APY as of June 2026?")
        self.assertIn("2.90%", a.text)

    def test_refusal_contract(self):
        a = self.ks.answer("What is the rate on a 7/1 adjustable-rate mortgage?")
        self.assertFalse(a.confident)
        self.assertFalse(re.search(r"\d", a.text))
        self.assertEqual(a.sources, [])
        self.assertIsNone(a.conflict_note)


class ScopeIsNotAConflict(unittest.TestCase):
    def test_business_and_personal_rates_differ_without_conflict(self):
        t = "# Rates\n\nRegular\n\n| Certificate | APY |\n| --- | --- |\n| 12 Month | {v} |\n\nRates are effective as of 06/02/2026.\n"
        ks = solution.build()
        ks.ingest(snap("01", "2026-09-28", {"/rates": t.format(v="3.40%"), "/business-rates": t.format(v="3.10%")}))
        self.assertEqual(ks.conflicts(), [])
        self.assertIn("3.40%", ks.answer("What is the 12 month certificate APY?").text)
        self.assertIn("3.10%", ks.answer("What is the business 12 month certificate APY?").text)


if __name__ == "__main__":
    unittest.main()
