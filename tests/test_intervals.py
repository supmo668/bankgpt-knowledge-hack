"""v2 validity intervals (NOTES.md, "Validity intervals"): every resolved fact has valid_from / valid_to, an
`as_of` between crawls lands on the stated effective date, ended offers close mid-gap, shadowed copies stay
stored and closed with the rule that beat them. Synthetic pages only."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "submission"))

from contract import Page, Snapshot  # noqa: E402

import solution  # noqa: E402

CU = "https://www.dupaco.com"
RATES = "# Rates\n\nRegular\n\n| Certificate | APY |\n| --- | --- |\n| 12 Month | {v} |\n\nRates are effective as of {e}.\n"
Q = "What is the 12 month certificate APY?"


def snap(sid, taken, pages):
    return Snapshot(sid, taken, [Page(url=CU + p, title=p.strip("/"), markdown=md, fetched_at=taken)
                                 for p, md in pages.items()])


def key_of(ks, needle="12 month"):
    return next(k for k in ks.store.timelines() if needle in k)


class EffectiveDateInsideTheGap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ks = solution.build()
        cls.ks.ingest(snap("01", "2026-05-12", {"/rates": RATES.format(v="2.90%", e="05/01/2026")}))
        cls.ks.ingest(snap("02", "2026-06-09", {"/rates": RATES.format(v="3.40%", e="06/02/2026")}))
        cls.ks.ingest(snap("03", "2026-08-27", {"/rates": RATES.format(v="3.60%", e="08/27/2026")}))

    def test_answer_switches_on_the_stated_date(self):
        a = lambda d: self.ks.answer(Q, as_of=d).text  # noqa: E731
        self.assertIn("2.90%", a("2026-06-01"))
        self.assertIn("3.40%", a("2026-06-02"))
        self.assertIn("3.40%", a("2026-06-08"))
        # effective on the crawl date itself: no interval view needed, the crawl view answers
        self.assertIn("3.40%", a("2026-08-26"))
        self.assertIn("3.60%", a("2026-08-27"))

    def test_source_cites_the_crawl_the_value_was_read_from(self):
        self.assertEqual(self.ks.answer(Q, as_of="2026-06-05").sources[0].snapshot_id, "02")
        self.assertEqual(self.ks.answer(Q, as_of="2026-06-01").sources[0].snapshot_id, "01")

    def test_timeline_intervals(self):
        st = self.ks.store
        vs = [f for f in st.timeline(key_of(self.ks)) if not f.shadowed]
        self.assertEqual([(f.valid_from, f.valid_to) for f in vs],
                         [("2026-05-01", "2026-06-02"), ("2026-06-02", "2026-08-27"), ("2026-08-27", None)])
        self.assertEqual([dict(f.value)["apy"] for f in vs], [(2.9,), (3.4,), (3.6,)])
        self.assertIn("replaced by", vs[0].closed_reason)
        self.assertIn("06/02/2026", vs[0].closed_reason)
        self.assertEqual(vs[-1].closed_reason, "")

    def test_facts_at_uses_the_interval_that_contains_the_date(self):
        st = self.ks.store
        k = key_of(self.ks)
        self.assertEqual(dict(st.facts_at("2026-06-05")[k].value)["apy"], (3.4,))
        self.assertEqual(dict(st.facts_at("2026-06-01")[k].value)["apy"], (2.9,))
        self.assertEqual(dict(st.facts_at(None)[k].value)["apy"], (3.6,))

    def test_conflicts_unchanged_by_interval_views(self):
        # one fact, three values over time: one conflict, resolved to the current value
        cs = [c for c in self.ks.conflicts() if "12" in c.topic]
        self.assertEqual(len(cs), 1)
        self.assertIn("3.6", cs[0].resolution)


class ObservationBeatsABackdatedEffectiveDate(unittest.TestCase):
    """The later crawl says 'effective 05/01' but the earlier crawl still showed the old value on 05-12: the
    observation wins, so the switch stays at the later crawl."""

    def test_backdated(self):
        ks = solution.build()
        ks.ingest(snap("01", "2026-05-12", {"/rates": RATES.format(v="2.90%", e="04/01/2026")}))
        ks.ingest(snap("02", "2026-06-09", {"/rates": RATES.format(v="3.40%", e="05/01/2026")}))
        self.assertIn("2.90%", ks.answer(Q, as_of="2026-06-08").text)
        self.assertIn("3.40%", ks.answer(Q, as_of="2026-06-09").text)
        vs = [f for f in ks.store.timeline(key_of(ks)) if not f.shadowed]
        self.assertEqual(vs[1].valid_from, "2026-06-09")

    def test_undated_change_starts_at_first_crawl_that_shows_it(self):
        t = "# Rates\n\n| Certificate | APY |\n| --- | --- |\n| 12 Month | {v} |\n"
        ks = solution.build()
        ks.ingest(snap("01", "2026-05-12", {"/rates": t.format(v="2.90%")}))
        ks.ingest(snap("02", "2026-06-09", {"/rates": t.format(v="3.40%")}))
        self.assertEqual(len(ks.store.views), 2)       # no interval view without a stated date
        self.assertIn("2.90%", ks.answer(Q, as_of="2026-06-08").text)
        vs = [f for f in ks.store.timeline(key_of(ks)) if not f.shadowed]
        self.assertEqual([(f.valid_from, f.valid_to) for f in vs], [("2026-05-12", "2026-06-09"), ("2026-06-09", None)])


class OfferEndsInsideTheGap(unittest.TestCase):
    PROMO = "# Promo\n\nSummer special 9-month certificate APY: 4.95% (offer ends 2026-07-31)\n"
    Q9 = "What APY does the 9-month certificate pay?"

    @classmethod
    def setUpClass(cls):
        cls.ks = solution.build()
        cls.ks.ingest(snap("01", "2026-06-09", {"/promo-lp": cls.PROMO}))
        cls.ks.ingest(snap("02", "2026-08-27", {"/promo-lp": cls.PROMO}))

    def test_offer_is_live_until_its_end_date_then_refused(self):
        self.assertIn("4.95%", self.ks.answer(self.Q9, as_of="2026-07-31").text)
        a = self.ks.answer(self.Q9, as_of="2026-08-01")
        self.assertFalse(a.confident, a.text)
        self.assertNotIn("4.95", a.text)

    def test_timeline_closes_on_the_end_date(self):
        k = next(k for k in self.ks.store.timelines() if "9-month" in k or "certificate apy" in k)
        live = [f for f in self.ks.store.timeline(k) if not f.shadowed]
        self.assertEqual(live[0].valid_to, "2026-08-01")
        self.assertIn("offer ended 2026-07-31", live[0].closed_reason)


class ShadowedCopiesStayStored(unittest.TestCase):
    def test_stale_page_is_closed_with_its_reason(self):
        stale = ("# Calculator\n\nRegular\n\n| Certificate | APY |\n| --- | --- |\n| 12 Month | 2.50% |\n\n"
                 "Rates are effective as of 01/01/2026.\n")
        ks = solution.build()
        ks.ingest(snap("01", "2026-05-12", {"/rates": RATES.format(v="2.90%", e="05/01/2026"),
                                            "/calculators/savings": stale}))
        sh = [f for f in ks.store.timeline(key_of(ks)) if f.shadowed]
        self.assertEqual(len(sh), 1)
        self.assertEqual(dict(sh[0].value)["apy"], (2.5,))
        self.assertTrue(sh[0].url.endswith("/calculators/savings"))
        self.assertIn("newer effective date", sh[0].closed_reason)
        self.assertNotIn("2.50", ks.answer(Q).text)


if __name__ == "__main__":
    unittest.main()


class CaptionQualifierSplitsProducts(unittest.TestCase):
    """Home Loan and Home Equity refinance tables repeat the same self-describing row labels; the caption word
    keeps them two facts, so one product's rate is never a stale copy of the other's."""

    MD = ("# Rates\n\nHome Loans - Refinance\n\n| Product | Rate | APR |\n| --- | --- | --- |\n"
          "| Refinance: 15-Year Fixed Conventional | 6.75% | 6.88% |\n\n"
          "Home Equity Refinance\n\n| Product | Rate | APR |\n| --- | --- | --- |\n"
          "| Refinance: 15-Year Fixed Conventional | 5.625% | 5.749% |\n")

    def test_two_keys_no_conflict(self):
        ks = solution.build()
        ks.ingest(snap("01", "2026-05-12", {"/rates": self.MD}))
        keys = [k for k in ks.store.timelines() if "15-year fixed conventional" in k]
        self.assertEqual(len(keys), 2, keys)
        self.assertTrue(any("home equity" in k for k in keys))
        self.assertFalse([c for c in ks.conflicts() if "15-Year" in c.topic])
