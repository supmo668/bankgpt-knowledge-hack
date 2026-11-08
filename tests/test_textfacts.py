"""v2 free-text facts (NOTES.md, "Free-text facts"): rate / APY / APR statements in paragraphs are resolved to
the table fact keys, checked against the table in the same crawl, and a disagreement is a conflict resolved by
the currency rules. Also: the newest dated award statement answers, and a sentence that refers back to the
program the previous sentence named stays with it. Synthetic pages only."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "submission"))

from contract import Page, Snapshot  # noqa: E402

import solution  # noqa: E402
from bankgpt.textfacts import TextFacts, extract_claims  # noqa: E402

CU = "https://www.dupaco.com"
MM_TABLE = ("# Rates\n\nMoney Market Rates\n\n| Account | Balance Tier | Dividend Rate | APY |\n| --- | --- | --- | --- |\n"
            "| Achieve Money Market | $0.01 - $24,999.99 | 0.00% | 0.00% |\n"
            "| Achieve Money Market | $25,000 - $99,999.99 | 3.20% | 3.25% |\n\n"
            "Rates are effective as of {e}.\n")
AUTO_TABLE = ("# Rates\n\nAuto Loans\n\n| Model Year | Term | APR |\n| --- | --- | --- |\n"
              "| 2024-27 Model Year | 60 Months | 5.39% APR |\n| 2021-23 Model Year | 60 Months | 5.64% APR |\n\n"
              "Rates are effective as of 08/31/2026.\n")


def snap(sid, taken, pages):
    return Snapshot(sid, taken, [Page(url=CU + p, title=p.strip("/") or "home", markdown=md, fetched_at=taken)
                                 for p, md in pages.items()])


def para_conflicts(ks):
    return [c for c in ks.conflicts() if "paragraph vs table" in c.topic]


class ClaimExtraction(unittest.TestCase):
    def test_values_columns_terms_amounts(self):
        cs = extract_claims(0, "Auto loan payment example for illustrative purposes only: If you borrow $30,000 at a "
                               "5.54% annual percentage rate (APR) with a 60-month term, your payment would be $573.")
        self.assertEqual(len(cs), 1)
        self.assertEqual((cs[0].col, cs[0].value, cs[0].terms, cs[0].amount), ("apr", (5.54,), (60,), 30000.0))

    def test_ranges_and_unit_first(self):
        cs = extract_claims(0, "The standard APR is a variable rate from 12.50% APR to 19.50% APR.")
        self.assertEqual([c.value for c in cs], [(12.5, 19.5)])
        cs = extract_claims(0, "Variable Annual Percentage Rate (APR) of 20.5%.")
        self.assertEqual([c.value for c in cs], [(20.5,)])

    def test_tier_before_each_value_and_stated_date(self):
        cs = extract_claims(0, "As of, 8/24/26, the tiered structure is: $0.00-$24,999.99 at 0.00% dividend rate and "
                               "0.00% APY; $25,000.00-99,999.99 at 4.00% dividend rate and 3.66% APY.")
        got = {(c.col, c.value, c.amount) for c in cs}
        self.assertIn(("apy", (3.66,), 62499.995), got)
        self.assertIn(("div", (4.0,), 62499.995), got)
        self.assertTrue(all(c.effective == "8/24/26" for c in cs))

    def test_bounds_and_intro(self):
        self.assertEqual(extract_claims(0, "The Interest Rate will never be less than 12.00% APR."), [])
        self.assertEqual(extract_claims(0, "with a floor rate as low as 4.00% APR."), [])
        cs = extract_claims(0, "The 2.90% annual percentage rate (APR) balance transfer introductory rate is valid.")
        self.assertTrue(cs[0].intro)
        self.assertTrue(extract_claims(0, "Get an introductory 2.90% APR on transfers.")[0].intro)

    def test_cash_bonus_is_not_a_balance(self):
        cs = extract_claims(0, "Earn $300 cash with checking PLUS 3.25% APY with an Achieve Money Market Account.")
        self.assertIsNone(cs[0].amount)


class ParagraphVsTable(unittest.TestCase):
    def test_disagreeing_paragraph_is_a_conflict_the_newer_dated_statement_wins(self):
        para = ("# Savings\n\nAchieve Money Market\n\nAs of, 8/24/26, the tiered rate structure is as follows: "
                "$25,000.00-99,999.99 at 4.00% dividend rate and 3.66% APY.\n")
        ks = solution.build()
        ks.ingest(snap("01", "2026-08-27", {"/membermoneymarket-lp": MM_TABLE.format(e="06/01/2026"),
                                            "/rates": para}))
        cs = para_conflicts(ks)
        self.assertEqual(len(cs), 1, [c.topic for c in ks.conflicts()])
        self.assertIn("3.66%", " ".join(cs[0].values))
        self.assertIn("3.25%", " ".join(cs[0].values))
        self.assertIn("3.66%", cs[0].resolution)          # rate sheet, stated 08/24 > landing table effective 06/01
        self.assertIn("outranks", cs[0].reason)

    def test_older_paragraph_loses_to_the_table(self):
        para = ("# News\n\nPublished March 2, 2026\n\nThe Achieve Money Market account earns 3.66% APY with at least "
                "$25,000.\n")
        ks = solution.build()
        ks.ingest(snap("01", "2026-08-27", {"/rates": MM_TABLE.format(e="08/01/2026"), "/newsroom/achieve": para}))
        cs = para_conflicts(ks)
        self.assertEqual(len(cs), 1)
        self.assertIn("3.25%", cs[0].resolution)
        self.assertNotIn("3.66", cs[0].resolution)

    def test_agreeing_paragraph_and_untiered_claim_matching_a_tier_are_not_conflicts(self):
        para = ("# Auto loans\n\nAuto loan payment example: If you borrow $30,000 at a 5.39% annual percentage rate "
                "(APR) with a 60-month term, your payment would be $571.54.\n\n"
                "The Achieve Money Market account earns 3.25% APY with at least $25,000.\n")
        ks = solution.build()
        ks.ingest(snap("01", "2026-09-28", {"/rates": AUTO_TABLE + "\n" + MM_TABLE.format(e="08/01/2026"),
                                            "/personal/loans/auto-loans": para}))
        self.assertEqual(para_conflicts(ks), [])

    def test_stale_payment_example_resolves_to_the_term_and_all_tiers(self):
        para = ("# Switch and earn\n\nAuto loan payment example: If you borrow $30,000 at a 5.34% annual percentage "
                "rate (APR) with a 60-month term, your payment would be $571.\n")
        ks = solution.build()
        ks.ingest(snap("01", "2026-09-28", {"/rates": AUTO_TABLE, "/switch-and-earn": para}))
        st = ks.store
        tf = TextFacts(st)
        [(cl, keys, tv)] = tf.disagreements(st.snapshot_for(None))
        self.assertEqual(len(keys), 2)
        self.assertTrue(all("60 months" in k for k in keys))
        cs = para_conflicts(ks)
        self.assertEqual(len(cs), 1)
        self.assertIn("5.39%", cs[0].resolution)

    def test_personal_page_claim_never_resolves_to_a_business_product(self):
        biz = ("# Business rates\n\nBusiness Platinum Visa\n\n| Card | APR |\n| --- | --- |\n"
               "| Business Platinum Visa | 15.50% |\n")
        para = "# Cards\n\nPlatinum Visa\n\nYour Platinum Visa rate is 12.50% APR.\n"
        ks = solution.build()
        ks.ingest(snap("01", "2026-09-28", {"/business/credit-cards": biz, "/personal/credit-cards": para}))
        self.assertEqual(para_conflicts(ks), [])

    def test_conflict_only_while_the_fact_is_on_the_site(self):
        para = ("# Switch and earn\n\nAuto loan payment example: If you borrow $30,000 at a 5.34% annual percentage "
                "rate (APR) with a 60-month term.\n")
        ks = solution.build()
        ks.ingest(snap("01", "2026-08-27", {"/rates": AUTO_TABLE, "/switch-and-earn": para}))
        ks.ingest(snap("02", "2026-09-28", {"/rates": "# Rates\n\nNo auto rates.\n",
                                            "/switch-and-earn": "# Switch\n\nNothing here.\n"}))
        self.assertEqual(para_conflicts(ks), [])


class AwardStatementsAndReferences(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rel = ("# Forbes ranks Dupaco as a top credit union\n\nPublished {d}\n\nDUBUQUE, Iowa— Forbes again named "
               "Dupaco one of the top-rated credit unions in America in its ranking of in-state institutions. "
               "This was the {n} time Forbes recognized Dupaco.\n")
        boiler = ("# Dupaco awards grants\n\nPublished January 8, 2026\n\nDupaco awards grants to nonprofits. It is a "
                  "Forbes Best-in-State Credit Union and a Top Workplace.\n")
        prog = ("# First-time homebuyers get a boost\n\nPublished January 5, 2026\n\nDupaco is partnering with the bank "
                "to help first-time homebuyers through the Home$tart program. This initiative provides up to $15,000 "
                "in assistance toward down payment and closing costs.\n")
        cls.ks = solution.build()
        cls.ks.ingest(snap("01", "2026-09-28", {
            "/newsroom/forbes-ranks-dupaco-2": rel.format(d="July 18, 2023", n="fourth"),
            "/newsroom/forbes-ranks-dupaco-3": rel.format(d="June 18, 2024", n="fifth"),
            "/newsroom/grants": boiler, "/newsroom/homestart": prog}))

    def test_newest_award_statement_answers(self):
        a = self.ks.answer("What recognition has Dupaco received from Forbes?")
        self.assertIn("fifth", a.text)
        self.assertNotIn("fourth", a.text)

    def test_sentence_referring_back_stays_with_its_program(self):
        a = self.ks.answer("What is Dupaco's Home$tart program?")
        self.assertIn("15,000", a.text)


class ValueCheckQuestions(unittest.TestCase):
    """'Do any savings tiers pay 0.35% APY?': the stated value is checked against the product, not used to find
    whichever product happens to carry it."""

    def test_value_is_checked_not_looked_up(self):
        t = ("# Rates\n\nShare Savings\n\n| Account | Balance Tier | APY |\n| --- | --- | --- |\n"
             "| Share Savings | $25,000+ | 0.20% |\n| Share Savings | $0 - $24,999.99 | 0.10% |\n\n"
             "Money Market\n\n| Account | Balance Tier | APY |\n| --- | --- | --- |\n"
             "| Investor's Choice Money Market | $25,000+ | 0.35% |\n")
        ks = solution.build()
        ks.ingest(snap("01", "2026-09-28", {"/rates": t}))
        a = ks.answer("Do any savings tiers pay 0.35% APY?")
        self.assertTrue(a.text.startswith("No:"), a.text)
        self.assertIn("0.20%", a.text)
        a = ks.answer("Do any savings tiers pay 0.20% APY?")
        self.assertTrue(a.text.startswith("Yes:"), a.text)


if __name__ == "__main__":
    unittest.main()
