"""v2 optional LLM contradiction check (NOTES.md): off by default, judges only the free-text pairs the
deterministic resolver left undecided, and caches verdicts by input hash. The API is never called here: the
single network function is replaced by a stub. Synthetic pages only."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "submission"))

from contract import Page, Snapshot  # noqa: E402

import solution  # noqa: E402
from bankgpt import llmcheck  # noqa: E402

CU = "https://www.dupaco.com"
TABLE = ("# Rates\n\nSummit Reserve Money Market\n\n| Account | Balance Tier | APY |\n| --- | --- | --- |\n"
         "| Summit Reserve Money Market | $25,000+ | 3.25% |\n")
# names the product with one distinctive word only: the strict rule needs two, so the pair is undecided
PARA = "# Savings\n\nSummit balances of $30,000 earn 3.66% APY this season.\n"


def build():
    ks = solution.build()
    ks.ingest(Snapshot("01", "2026-09-28", [Page(url=CU + p, title=p.strip("/"), markdown=md, fetched_at="2026-09-28")
                                            for p, md in {"/rates": TABLE, "/personal/savings": PARA}.items()]))
    return ks


def para(ks):
    return [c for c in ks.conflicts() if "paragraph vs table" in c.topic]


class LlmCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"BANKGPT_LLM_CACHE": os.path.join(self.tmp.name, "c.json")})
        self.env.start()
        llmcheck._cache = None

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
        llmcheck._cache = None

    def test_off_by_default_no_call(self):
        os.environ.pop("BANKGPT_LLM_CHECK", None)
        with mock.patch.object(llmcheck, "_ask", side_effect=AssertionError("called")) as ask:
            self.assertEqual(para(build()), [])
            ask.assert_not_called()

    def test_undecided_pair_is_judged_and_cached(self):
        os.environ["BANKGPT_LLM_CHECK"] = "1"
        verdict = {"same_fact": True, "contradicts": True}
        with mock.patch.object(llmcheck, "_ask", return_value=verdict) as ask:
            cs = para(build())
            self.assertEqual(len(cs), 1)
            self.assertIn("optional LLM check", cs[0].reason)
            self.assertEqual(cs[0].resolution, "3.25% APY")
            n = ask.call_count
            self.assertGreaterEqual(n, 1)
        # a fresh process (cache reloaded from disk) gives the same conflicts without calling the API
        llmcheck._cache = None
        with mock.patch.object(llmcheck, "_ask", side_effect=AssertionError("called")):
            cs2 = para(build())
        self.assertEqual([(c.topic, c.values, c.resolution) for c in cs], [(c.topic, c.values, c.resolution) for c in cs2])

    def test_judge_says_different_fact(self):
        os.environ["BANKGPT_LLM_CHECK"] = "1"
        with mock.patch.object(llmcheck, "_ask", return_value={"same_fact": False, "contradicts": False}):
            self.assertEqual(para(build()), [])

    def test_failure_is_no_verdict(self):
        os.environ["BANKGPT_LLM_CHECK"] = "1"
        with mock.patch.object(llmcheck, "_ask", return_value=None):
            self.assertEqual(para(build()), [])
        self.assertEqual(llmcheck._load(), {})      # failures are not cached

    def test_failed_pair_is_asked_once_per_process(self):
        # no key or no network: one attempt per distinct pair in a run, not one per view that holds it
        os.environ["BANKGPT_LLM_CHECK"] = "1"
        with mock.patch.object(llmcheck, "_ask", return_value=None) as ask:
            for _ in range(3):
                self.assertIsNone(llmcheck.judge("s", "q", "r"))
            self.assertEqual(ask.call_count, 1)
        llmcheck._cache = None                       # a new process tries again
        with mock.patch.object(llmcheck, "_ask", return_value=None) as ask:
            llmcheck.judge("s", "q", "r")
            self.assertEqual(ask.call_count, 1)

    def test_resolved_pairs_never_reach_the_judge(self):
        os.environ["BANKGPT_LLM_CHECK"] = "1"
        strong = "# Savings\n\nThe Summit Reserve Money Market account earns 3.66% APY with at least $30,000.\n"
        ks = solution.build()
        with mock.patch.object(llmcheck, "_ask", side_effect=AssertionError("called")):
            ks.ingest(Snapshot("01", "2026-09-28", [
                Page(url=CU + "/rates", title="rates", markdown=TABLE, fetched_at="2026-09-28"),
                Page(url=CU + "/personal/savings", title="savings", markdown=strong, fetched_at="2026-09-28")]))
            self.assertEqual(len(para(ks)), 1)      # decided by the rules alone

    def test_cache_key_depends_on_every_input(self):
        k = llmcheck.cache_key("m", "s", "q", "r")
        self.assertEqual(k, llmcheck.cache_key("m", "s", "q", "r"))
        self.assertEqual(len({k, llmcheck.cache_key("m2", "s", "q", "r"), llmcheck.cache_key("m", "s2", "q", "r"),
                              llmcheck.cache_key("m", "s", "q2", "r"), llmcheck.cache_key("m", "s", "q", "r2")}), 5)


if __name__ == "__main__":
    unittest.main()
