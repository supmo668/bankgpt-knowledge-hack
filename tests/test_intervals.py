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


T12 = "# Rates\n\nRegular\n\n| Certificate | APY |\n| --- | --- |\n| 12 Month | 2.90% |\n"
T24 = "# Specials\n\nSpecials\n\n| Certificate | APY |\n| --- | --- |\n| 24 Month | {v} |\n\nRates are effective as of {e}.\n"
Q24 = "What is the 24 month certificate APY?"


class BackdatedClaimCannotReachOverACrawlThatLackedIt(unittest.TestCase):
    """F1: a value whose page states an effective date before crawls that did not show the fact starts at the
    first view that shows it, so `facts_at` and `answer` agree."""

    def test_new_fact_backdated_before_the_earlier_crawl(self):
        ks = solution.build()
        ks.ingest(snap("01", "2026-05-12", {"/rates": T12}))
        ks.ingest(snap("02", "2026-06-09", {"/rates": T12, "/specials": T24.format(v="4.10%", e="05/03/2026")}))
        k = key_of(ks, "24 month")
        vs = [f for f in ks.store.timeline(k) if not f.shadowed]
        self.assertEqual(vs[0].valid_from, "2026-06-09")
        self.assertNotIn(k, ks.store.facts_at("2026-05-20"))
        self.assertFalse(ks.answer(Q24, as_of="2026-05-20").confident)

    def test_fact_gone_then_back_with_a_date_inside_the_absence(self):
        ks = solution.build()
        ks.ingest(snap("01", "2026-05-12", {"/rates": T12, "/specials": T24.format(v="3.90%", e="05/01/2026")}))
        # the page is crawled without the row (a page missing from a crawl would be carried forward instead)
        ks.ingest(snap("02", "2026-05-19", {"/rates": T12, "/specials": "# Specials\n\nNo specials right now.\n"}))
        ks.ingest(snap("03", "2026-06-09", {"/rates": T12, "/specials": "# Specials\n\nNo specials right now.\n"}))
        ks.ingest(snap("04", "2026-08-27", {"/rates": T12, "/specials": T24.format(v="4.10%", e="06/01/2026")}))
        k = key_of(ks, "24 month")
        vs = [(f.valid_from, f.valid_to) for f in ks.store.timeline(k) if not f.shadowed]
        self.assertEqual(vs, [("2026-05-01", "2026-05-19"), ("2026-08-27", None)])
        self.assertNotIn(k, ks.store.facts_at("2026-06-05"))
        self.assertFalse(ks.answer(Q24, as_of="2026-06-05").confident)


class OfferEndsAfterTheLastCrawl(unittest.TestCase):
    """F2/F4: an offer the newest crawl still shows stops being quoted the day after its stated end date; while
    it runs, its version carries the scheduled end, not an 'ended' reason."""
    PROMO = OfferEndsInsideTheGap.PROMO.replace("2026-07-31", "2026-08-27")
    Q9 = OfferEndsInsideTheGap.Q9

    @classmethod
    def setUpClass(cls):
        cls.ks = solution.build()
        cls.ks.ingest(snap("01", "2026-08-20", {"/promo-lp": cls.PROMO}))

    def test_refused_after_the_end_date(self):
        self.assertIn("4.95%", self.ks.answer(self.Q9, as_of="2026-08-27").text)
        self.assertIn("4.95%", self.ks.answer(self.Q9).text)        # no as_of: the newest crawl, offer live
        for d in ("2026-08-28", "2026-09-15"):
            a = self.ks.answer(self.Q9, as_of=d)
            self.assertFalse(a.confident, a.text)
            self.assertNotIn("4.95", a.text)
        self.assertEqual({k for k in self.ks.store.facts_at("2026-09-15") if "9-month" in k}, set())

    def test_scheduled_end_is_not_reported_as_ended(self):
        k = next(k for k in self.ks.store.timelines() if "9-month" in k)
        live = [f for f in self.ks.store.timeline(k) if not f.shadowed]
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0].valid_to, "2026-08-28")
        self.assertIn("stated end date", live[0].closed_reason)


class OfferAlreadyEndedIsNeverCurrent(unittest.TestCase):
    """F3: an offer shown after its own end date never yields a version with valid_to before valid_from."""

    def test_no_inverted_interval(self):
        ks = solution.build()
        ks.ingest(snap("01", "2026-09-01", {"/promo-lp": "# Promo\n\nPromo certificate APY: 4.00% (offer ends 2026-06-30)\n"}))
        for vs in ks.store.timelines().values():
            for f in vs:
                self.assertTrue(f.valid_to is None or f.valid_from < f.valid_to, f)


class ShadowedCopyGoneAndBack(unittest.TestCase):
    """F7: a stale copy that leaves the site and comes back is two shadowed versions, not one span over the gap."""

    def test_two_shadowed_versions(self):
        stale = ("# Calculator\n\nRegular\n\n| Certificate | APY |\n| --- | --- |\n| 12 Month | 2.50% |\n\n"
                 "Rates are effective as of 01/01/2026.\n")
        rates = RATES.format(v="2.90%", e="05/01/2026")
        ks = solution.build()
        ks.ingest(snap("01", "2026-05-12", {"/rates": rates, "/calculators/savings": stale}))
        ks.ingest(snap("02", "2026-06-09", {"/rates": rates, "/calculators/savings": "# Calculator\n\nTry it.\n"}))
        ks.ingest(snap("03", "2026-08-27", {"/rates": rates, "/calculators/savings": stale}))
        sh = [(f.valid_from, f.valid_to) for f in ks.store.timeline(key_of(ks)) if f.shadowed]
        self.assertEqual(sh, [("2026-05-12", "2026-06-09"), ("2026-08-27", None)])


if __name__ == "__main__":
    unittest.main()
