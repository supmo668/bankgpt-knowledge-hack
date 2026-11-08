"""v2 free-text facts (NOTES.md, "Free-text facts"): rate / APY / APR statements in paragraphs are resolved to
the table fact keys, checked against the table in the same crawl, and a disagreement is a conflict resolved by
the currency rules. Also: the newest dated award statement answers, a sentence that refers back to the program
the previous sentence named stays with it, and a yes/no value check compares the asked column over the whole
table. Synthetic pages, made-up products and organisations only."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "submission"))

from contract import Page, Snapshot  # noqa: E402

import solution  # noqa: E402
from bankgpt.textfacts import TextFacts, _tier, extract_claims  # noqa: E402

CU = "https://www.dupaco.com"
MM_TABLE = ("# Rates\n\nMoney Market Rates\n\n| Account | Balance Tier | Dividend Rate | APY |\n| --- | --- | --- | --- |\n"
            "| Summit Money Market | $0.01 - $24,999.99 | 0.00% | 0.00% |\n"
            "| Summit Money Market | $25,000 - $99,999.99 | 3.20% | 3.25% |\n\n"
            "Rates are effective as of {e}.\n")
AUTO_TABLE = ("# Rates\n\nAuto Loans\n\n| Model Year | Term | APR |\n| --- | --- | --- |\n"
              "| 2024-27 Model Year | 60 Months | 5.39% APR |\n| 2021-23 Model Year | 60 Months | 5.64% APR |\n"
              "| 2024-27 Model Year | 72 Months | 5.49% APR |\n\nRates are effective as of 08/31/2026.\n")
CARD_TABLE = ("# Rates\n\nCards\n\n| Card | Intro APR | APR |\n| --- | --- | --- |\n"
              "| Comet Visa | 2.90% | 12.50% |\n")


def snap(sid, taken, pages):
    return Snapshot(sid, taken, [Page(url=CU + p, title=p.strip("/") or "home", markdown=md, fetched_at=taken)
                                 for p, md in pages.items()])


def para_conflicts(ks):
    return [c for c in ks.conflicts() if "paragraph vs table" in c.topic]


def one_view(pages, taken="2026-09-28"):
    ks = solution.build()
    ks.ingest(snap("01", taken, pages))
    st = ks.store
    return ks, st, TextFacts(st), st.snapshot_for(None)


class ClaimExtraction(unittest.TestCase):
    def test_values_columns_terms_amounts(self):
        cs = extract_claims(0, "Auto loan payment example for illustrative purposes only: If you borrow $30,000 at a "
                               "5.54% annual percentage rate (APR) with a 60-month term, your payment would be $573.")
        self.assertEqual(len(cs), 1)
        self.assertEqual((cs[0].col, cs[0].value, cs[0].terms, cs[0].amount), ("apr", (5.54,), (60,), 30000.0))

    def test_ranges_and_unit_first(self):
        self.assertEqual([c.value for c in extract_claims(0, "A variable rate from 12.50% APR to 19.50% APR.")],
                         [(12.5, 19.5)])
        self.assertEqual([c.value for c in extract_claims(0, "Variable Annual Percentage Rate (APR) of 20.5%.")],
                         [(20.5,)])
        self.assertNotIn((0.0,), [c.value for c in extract_claims(0, "Pay down 100% APR debt.")])

    def test_tier_before_each_value(self):
        cs = extract_claims(0, "The tiered structure is: $0.00-$24,999.99 at 0.00% dividend rate and 0.00% APY; "
                               "$25,000.00-99,999.99 at 4.00% dividend rate and 3.66% APY.")
        got = {(c.col, c.value, c.amount) for c in cs}
        self.assertIn(("apy", (3.66,), 62499.995), got)
        self.assertIn(("div", (4.0,), 62499.995), got)

    def test_tier_wins_over_an_earlier_opening_amount(self):
        cs = extract_claims(0, "Open with $500 and the tiers are $25,000.00-99,999.99 at 4.00% APY.")
        self.assertEqual(cs[0].amount, 62499.995)

    def test_amount_after_the_value(self):
        cs = extract_claims(0, "The Summit Money Market account earns 3.66% APY with at least $25,000.")
        self.assertEqual(cs[0].amount, 25000.0)

    def test_stated_date_belongs_to_its_sentence(self):
        cs = extract_claims(0, "As of 8/24/26, Summit pays 3.66% APY. Certificates earn 4.10% APY.")
        self.assertEqual([c.effective for c in cs], ["8/24/26", None])

    def test_terms_belong_to_their_value(self):
        cs = extract_claims(0, "The intro rate of 2.90% APR applies for 6 months, then a standard rate of "
                               "15.50% APR applies to all balances on the card account going forward.")
        self.assertEqual({c.value: c.terms for c in cs}[(15.5,)], ())

    def test_bounds_hypotheticals_and_intro(self):
        for s in ("The Interest Rate will never be less than 12.00% APR.", "with a floor rate as low as 4.00% APR.",
                  "The maximum rate is 18.00% APR.", "Imagine earning 9.00% APY.", "The national average is 0.45% APY.",
                  "If the member does not qualify, the APR is 8.00% APR."):
            self.assertEqual(extract_claims(0, s), [], s)
        self.assertTrue(extract_claims(0, "The 2.90% annual percentage rate (APR) balance transfer rate applies.")[0].intro)
        self.assertTrue(extract_claims(0, "Get 1.99% APR for 12 months on balance transfers.")[0].intro)
        cs = extract_claims(0, "The introductory rate structure is as follows: $25,000.00-99,999.99 at 3.66% APY.")
        self.assertTrue(cs[0].intro)

    def test_cash_bonus_is_not_a_balance(self):
        self.assertIsNone(extract_claims(0, "Earn $300 cash with checking PLUS 3.25% APY with a money market.")[0].amount)
        self.assertIsNone(extract_claims(0, "Open it with $300 cash bonus and earn 3.25% APY.")[0].amount)

    def test_or_less_tier(self):
        self.assertEqual(_tier("personal | home | 30-year fixed conventional $300000 or less"), (0.0, 300000.0))


class Resolution(unittest.TestCase):
    def test_term_tier_column_and_product_filters(self):
        para = ("# Loans\n\nAuto loan example: If you borrow $30,000 at a 5.99% annual percentage rate (APR) with an "
                "84-month term.\n\nThe Summit Money Market account earns 3.25% APY with at least $5,000.\n\n"
                "A savings account earns 3.00% APY.\n\nThe Summit Money Market account has a 4.00% APR.\n")
        ks, st, tf, v = one_view({"/rates": AUTO_TABLE + "\n" + MM_TABLE.format(e="08/01/2026"),
                                  "/personal/loans/info": para})
        claims = {cl.quote: cl for cid, c in enumerate(st.chunks) if c.kind == "para" for cl in tf.claims_of(cid)}
        get = lambda q: next(cl for k, cl in claims.items() if q in k)  # noqa: E731
        self.assertEqual(tf.resolve(get("5.99%"), v), [])                       # no 84-month key
        self.assertEqual(tf.resolve(get("3.25%"), v), ["personal | summit money market | $0.01-$24999.99"])
        self.assertEqual(tf.resolve(get("3.00%"), v), [])                       # generic words only
        self.assertEqual(tf.resolve(get("4.00%"), v), [])                       # APR column the table lacks

    def test_intro_and_standard_columns(self):
        para = ("# Cards\n\nComet Visa\n\nThe Comet Visa standard rate is 13.50% APR.\n\n"
                "The Comet Visa introductory rate is 2.90% APR.\n\nThe Comet Visa carries a 12.50% APR.\n")
        ks, st, tf, v = one_view({"/rates": CARD_TABLE, "/personal/cards": para})
        cs = para_conflicts(ks)
        self.assertEqual(len(cs), 1, [c.values for c in cs])                   # only the 13.50% standard claim
        self.assertEqual(cs[0].resolution, "12.5% APR")
        self.assertIn("13.5% APR", cs[0].values)

    def test_personal_page_claim_never_resolves_to_a_business_product(self):
        biz = ("# Business rates\n\nBusiness Comet Visa\n\n| Card | APR |\n| --- | --- |\n"
               "| Business Comet Visa | 15.50% |\n")
        ks, st, tf, v = one_view({"/business/credit-cards": biz,
                                  "/personal/credit-cards": "# Cards\n\nComet Visa\n\nYour Comet Visa rate is 12.50% APR.\n"})
        cl = next(cl for cid, c in enumerate(st.chunks) if c.kind == "para" for cl in tf.claims_of(cid))
        self.assertEqual(tf.resolve(cl, v), [])
        self.assertEqual(para_conflicts(ks), [])

    def test_repeated_disclosure_is_not_resolved_through_its_tab_heading(self):
        note = "The rate shown may change after account opening and is 6.25% APR for most members today."
        page = "# Rates\n\n## Summit Money Market\n\n" + note + "\n\n## Comet Visa\n\n" + note + "\n"
        ks, st, tf, v = one_view({"/rates": CARD_TABLE, "/personal/rates-info": page})
        self.assertEqual(para_conflicts(ks), [])

    def test_range_claim_agrees_with_one_end_and_with_the_tier_span(self):
        t = ("# Rates\n\nComet Visa Rates\n\n| Card | APR |\n| --- | --- |\n| Comet Visa | 12.50% – 19.50% |\n")
        ks, st, tf, v = one_view({"/rates": t, "/personal/cards": "# Cards\n\nComet Visa\n\nThe Comet Visa rate starts "
                                                                    "at 12.50% APR.\n"})
        self.assertEqual(para_conflicts(ks), [])

    def test_claims_on_calculator_pages_are_ignored(self):
        ks, st, tf, v = one_view({"/rates": MM_TABLE.format(e="08/01/2026"),
                                  "/calculators/savings": "# Calculator\n\nThe Summit Money Market account earns "
                                                          "9.00% APY with at least $25,000.\n"})
        self.assertEqual(para_conflicts(ks), [])


class ParagraphVsTable(unittest.TestCase):
    def test_disagreeing_paragraph_is_a_conflict_the_newer_dated_statement_wins(self):
        para = ("# Savings\n\nSummit Money Market\n\nAs of, 8/24/26, the Summit Money Market tiers are: "
                "$25,000.00-99,999.99 at 3.30% dividend rate and 3.36% APY.\n")
        ks = solution.build()
        ks.ingest(snap("01", "2026-08-27", {"/membermoneymarket-lp": MM_TABLE.format(e="06/01/2026"), "/rates": para}))
        cs = para_conflicts(ks)
        self.assertEqual(len(cs), 1, [c.topic for c in ks.conflicts()])
        self.assertIn("3.36%", " ".join(cs[0].values))
        self.assertIn("3.25%", " ".join(cs[0].values))
        self.assertIn("3.36%", cs[0].resolution)          # rate sheet, stated 08/24 > landing table effective 06/01
        self.assertIn("outranks", cs[0].reason)
        self.assertIn("$25,000 - $99,999.99", cs[0].topic)  # the tier the claim resolved to

    def test_older_paragraph_loses_to_the_table(self):
        para = ("# News\n\nPublished March 2, 2026\n\nThe Summit Money Market account earns 3.66% APY with at least "
                "$25,000.\n")
        ks = solution.build()
        ks.ingest(snap("01", "2026-08-27", {"/rates": MM_TABLE.format(e="08/01/2026"), "/newsroom/summit": para}))
        cs = para_conflicts(ks)
        self.assertEqual(len(cs), 1)
        self.assertEqual(cs[0].resolution, "3.25% APY")

    def test_agreeing_paragraph_and_untiered_claim_matching_a_tier_are_not_conflicts(self):
        para = ("# Auto loans\n\nAuto loan payment example: If you borrow $30,000 at a 5.39% annual percentage rate "
                "(APR) with a 60-month term, your payment would be $571.54.\n\n"
                "The Summit Money Market account earns 3.25% APY with at least $25,000.\n")
        ks = solution.build()
        ks.ingest(snap("01", "2026-09-28", {"/rates": AUTO_TABLE + "\n" + MM_TABLE.format(e="08/01/2026"),
                                            "/personal/loans/auto-loans": para}))
        self.assertEqual(para_conflicts(ks), [])

    def test_stale_payment_example_resolves_to_the_term_and_all_tiers(self):
        para = ("# Switch and earn\n\nAuto loan payment example: If you borrow $30,000 at a 5.34% annual percentage "
                "rate (APR) with a 60-month term, your payment would be $571.\n")
        ks, st, tf, v = one_view({"/rates": AUTO_TABLE, "/switch-and-earn": para})
        [(cl, keys, tv)] = tf.disagreements(v)
        self.assertEqual(len(keys), 2)
        self.assertTrue(all("60 months" in k for k in keys))
        cs = para_conflicts(ks)
        self.assertEqual(len(cs), 1)
        self.assertIn("5.39%", cs[0].resolution)
        self.assertNotRegex(cs[0].topic, r"Model Year|\d{4}-\d\d")   # a claim naming no tier: no tier in the label

    def test_paragraph_gone_loses_and_current_table_value_resolves(self):
        para = ("# Switch and earn\n\nAuto loan payment example: If you borrow $30,000 at a 5.34% annual percentage "
                "rate (APR) with a 60-month term.\n")
        newer = AUTO_TABLE.replace("5.39%", "5.45%")
        ks = solution.build()
        ks.ingest(snap("01", "2026-08-27", {"/rates": AUTO_TABLE, "/switch-and-earn": para}))
        ks.ingest(snap("02", "2026-09-28", {"/rates": newer, "/switch-and-earn": "# Switch\n\nNew offers soon.\n"}))
        cs = para_conflicts(ks)
        self.assertEqual(len(cs), 1)
        self.assertIn("no longer on the site", cs[0].reason)
        self.assertIn("5.45%", cs[0].resolution)
        self.assertNotIn("5.39", cs[0].resolution)

    def test_conflict_only_while_the_fact_is_on_the_site(self):
        para = ("# Switch and earn\n\nAuto loan payment example: If you borrow $30,000 at a 5.34% annual percentage "
                "rate (APR) with a 60-month term.\n")
        ks = solution.build()
        ks.ingest(snap("01", "2026-08-27", {"/rates": AUTO_TABLE, "/switch-and-earn": para}))
        ks.ingest(snap("02", "2026-09-28", {"/rates": "# Rates\n\nNo auto rates.\n",
                                            "/switch-and-earn": "# Switch\n\nNothing here.\n"}))
        self.assertEqual(para_conflicts(ks), [])


AWARD = ("# Kiplinger names Dupaco a top credit union\n\nPublished {d}\n\nDUBUQUE, Iowa— Kiplinger again named Dupaco "
         "one of the best credit unions in its region in a ranking based on member reviews. This was the {n} time "
         "Kiplinger recognized Dupaco.\n")
FILLER = ("Dupaco has served members for decades and keeps adding new services across the region. Members can open "
          "accounts online or at any branch and get help from local staff. ")


class AwardStatements(unittest.TestCase):
    def test_newest_award_statement_answers(self):
        boiler = ("# Dupaco funds local projects\n\nPublished January 8, 2026\n\nDupaco funds local projects. It is a "
                  "Kiplinger best regional credit union and a regional top workplace.\n")
        ks, *_ = one_view({"/newsroom/kiplinger-2": AWARD.format(d="July 18, 2023", n="second"),
                           "/newsroom/kiplinger-3": AWARD.format(d="June 18, 2024", n="third"),
                           "/newsroom/projects": boiler})
        a = ks.answer("What ranking has Kiplinger given Dupaco?")
        self.assertIn("third", a.text)
        self.assertNotIn("second", a.text)

    def test_org_needs_an_award_word(self):
        from bankgpt.answer import intent_of
        self.assertIsNone(intent_of("Where does Kiplinger publish its list?").org)
        self.assertEqual(intent_of("Has Kiplinger ranked Dupaco?").org, "Kiplinger")


class BackReferences(unittest.TestCase):
    PROG = ("# Neighbors first: Dupaco offers BrightPath grants\n\nPublished January 5, 2026\n\n" + FILLER * 2 +
            "Dupaco is partnering with a regional bank to help first-time buyers through the BrightPath program. "
            "This grant covers up to $9,500 of closing costs for eligible buyers. " + FILLER * 2 + "\n")

    def test_sentence_referring_back_stays_with_its_program(self):
        ks, *_ = one_view({"/newsroom/brightpath": self.PROG})
        self.assertIn("9,500", ks.answer("What is the BrightPath program from Dupaco?").text)

    def test_unrelated_next_sentence_is_not_appended(self):
        prog = self.PROG.replace("This grant covers", "Separately, the branch lobby covers")
        ks, *_ = one_view({"/newsroom/brightpath": prog})
        self.assertNotIn("lobby", ks.answer("What is the BrightPath program from Dupaco?").text)

    def test_bare_the_sentence_is_not_a_back_reference(self):
        from bankgpt.answer import ANAPHOR_RE
        self.assertIsNone(ANAPHOR_RE.match("The arc represents the warm glow of the sun."))
        self.assertTrue(ANAPHOR_RE.match("This grant covers closing costs."))
        self.assertTrue(ANAPHOR_RE.match("The program pays up to $500."))


class ValueCheckQuestions(unittest.TestCase):
    """'Does any club account earn 1.25% APY?': the stated value is checked against the product's whole table in
    the column asked, not used to find whichever product happens to carry it."""
    T = ("# Rates\n\nClub Savings\n\n| Account | Balance Tier | Dividend Rate | APY |\n| --- | --- | --- | --- |\n"
         "| Club Savings | $25,000+ | 1.20% | 1.25% |\n| Club Savings | $0 - $24,999.99 | 0.50% | 0.51% |\n\n"
         "Money Market\n\n| Account | Balance Tier | APY |\n| --- | --- | --- |\n"
         "| Orbit Money Market | $25,000+ | 1.75% |\n\nCards\n\n| Card | APR |\n| --- | --- |\n"
         "| Comet Visa | 12.50% – 19.50% |\n")

    @classmethod
    def setUpClass(cls):
        cls.ks, *_ = one_view({"/rates": cls.T})

    def lead(self, q):
        return self.ks.answer(q).text.split(".")[0]

    def test_value_is_checked_not_looked_up(self):
        self.assertTrue(self.lead("Does any club savings tier earn 1.75% APY?").startswith("No"))
        self.assertTrue(self.lead("Does any club savings tier earn 1.25% APY?").startswith("Yes"))

    def test_only_the_asked_column(self):
        self.assertTrue(self.lead("Does any club savings tier earn 1.20% APY?").startswith("No"))   # a dividend rate

    def test_value_inside_a_range(self):
        self.assertTrue(self.lead("Is there a Comet Visa at 15% APR?").startswith("Yes"))

    def test_comparisons_get_no_lead(self):
        a = self.ks.answer("Does any club savings tier earn at least 1.00% APY?").text
        self.assertFalse(a.startswith(("Yes", "No")), a)


if __name__ == "__main__":
    unittest.main()
