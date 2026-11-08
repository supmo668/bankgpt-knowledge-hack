"""v2 optional LLM contradiction check (NOTES.md): off by default, judges only the free-text pairs the
deterministic resolver left undecided, and caches verdicts by input hash. The API is never called here: the
single network function is replaced by a stub. Synthetic pages only."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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
        llmcheck._failed.clear()
        llmcheck.calls = 0

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
        llmcheck._cache = None
        llmcheck._failed.clear()

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
            self.assertEqual(ask.call_count, 1)        # one distinct pair, one call
            self.assertIn("3.66% APY", cs[0].values)
        self.assertEqual(len(json.loads(Path(os.environ["BANKGPT_LLM_CACHE"]).read_text())), 1)
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
        self.assertFalse(Path(os.environ["BANKGPT_LLM_CACHE"]).exists())   # failures never reach the disk

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
            self.assertNotIn("optional LLM check", para(ks)[0].reason)

    def test_cache_key_depends_on_every_input(self):
        k = llmcheck.cache_key("m", "s", "q", "r")
        self.assertEqual(k, llmcheck.cache_key("m", "s", "q", "r"))
        self.assertEqual(len({k, llmcheck.cache_key("m2", "s", "q", "r"), llmcheck.cache_key("m", "s2", "q", "r"),
                              llmcheck.cache_key("m", "s", "q2", "r"), llmcheck.cache_key("m", "s", "q", "r2")}), 5)

    # -- QG it. 1.3 -----------------------------------------------------------------------------------------------

    def test_only_both_true_makes_a_conflict(self):
        os.environ["BANKGPT_LLM_CHECK"] = "1"
        for v in ({"same_fact": False, "contradicts": True}, {"same_fact": True, "contradicts": False}):
            with self.subTest(v=v):
                llmcheck._cache = None
                Path(os.environ["BANKGPT_LLM_CACHE"]).unlink(missing_ok=True)
                with mock.patch.object(llmcheck, "_ask", return_value=v):
                    self.assertEqual(para(build()), [])

    def test_flag_must_be_exactly_one(self):
        for val in ("0", "", "true", "yes", " 1"):
            with self.subTest(val=val):
                os.environ["BANKGPT_LLM_CHECK"] = val
                self.assertFalse(llmcheck.enabled())
                with mock.patch.object(llmcheck, "_ask", side_effect=AssertionError("called")):
                    self.assertIsNone(llmcheck.judge("s", "q", "r"))
                    self.assertEqual(para(build()), [])

    def test_tierless_claim_matching_one_tier_is_not_judged(self):
        # a sentence that names no tier agrees with the product when any of its tiers carries the value
        os.environ["BANKGPT_LLM_CHECK"] = "1"
        table = ("# Rates\n\nSummit Reserve Money Market\n\n| Account | Balance Tier | APY |\n| --- | --- | --- |\n"
                 "| Summit Reserve Money Market | $10,000-$24,999 | 3.25% |\n"
                 "| Summit Reserve Money Market | $25,000+ | 3.66% |\n")
        ks = solution.build()
        with mock.patch.object(llmcheck, "_ask", return_value={"same_fact": True, "contradicts": True}) as ask:
            ks.ingest(Snapshot("01", "2026-09-28", [
                Page(url=CU + "/rates", title="rates", markdown=table, fetched_at="2026-09-28"),
                Page(url=CU + "/personal/savings", title="savings",
                     markdown="# Savings\n\nSummit balances earn 3.66% APY this season.\n", fetched_at="2026-09-28")]))
            self.assertEqual(para(ks), [])
            ask.assert_not_called()

    def test_repeated_disclosure_is_not_judged(self):
        # the rules ignore a paragraph repeated under several headings; the optional check must too
        os.environ["BANKGPT_LLM_CHECK"] = "1"
        md = ("# Savings\n\n## Summit Reserve\n\nBalances earn 3.66% APY this season.\n\n"
              "## Harbor Plus\n\nBalances earn 3.66% APY this season.\n")
        ks = solution.build()
        with mock.patch.object(llmcheck, "_ask", return_value={"same_fact": True, "contradicts": True}) as ask:
            ks.ingest(Snapshot("01", "2026-09-28", [
                Page(url=CU + "/rates", title="rates", markdown=TABLE, fetched_at="2026-09-28"),
                Page(url=CU + "/personal/savings", title="savings", markdown=md, fetched_at="2026-09-28")]))
            self.assertEqual(para(ks), [])
            ask.assert_not_called()

    def test_bad_cache_file_is_no_verdict_not_a_crash(self):
        os.environ["BANKGPT_LLM_CHECK"] = "1"
        path = Path(os.environ["BANKGPT_LLM_CACHE"])
        key = llmcheck.cache_key(llmcheck.DEFAULT_MODEL, "s", "q", "r")
        for body in ("null", "[]", '"x"', '{"ab', json.dumps({key: "yes"}),
                     json.dumps({key: {"same_fact": "true", "contradicts": True}})):
            with self.subTest(body=body):
                path.write_text(body)
                llmcheck._cache = None
                with mock.patch.object(llmcheck, "_ask", return_value={"same_fact": True, "contradicts": True}):
                    self.assertEqual(llmcheck.judge("s", "q", "r"), {"same_fact": True, "contradicts": True})
                self.assertIsInstance(json.loads(path.read_text()), dict)

    def _fake_sdk(self, text=None, stop="end_turn", raises=None):
        made = {}

        class Client:
            def __init__(self, **kw):
                made["client"] = kw
                self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

            def create(self, **kw):
                made["request"] = kw
                if raises:
                    raise raises
                return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text or "")])
        return SimpleNamespace(Anthropic=Client), made

    def test_reply_parsing(self):
        ok = {"same_fact": True, "contradicts": False}
        cases = [('{"same_fact": true, "contradicts": false}', "end_turn", None, ok),
                 ('Here: {"same_fact": true, "contradicts": false, "extra": 1} done', "end_turn", None, ok),
                 ('{"same_fact": true, "contradicts": false}', "refusal", None, None),
                 ("no json here", "end_turn", None, None),
                 ('{"same_fact": tru', "end_turn", None, None),
                 ('{"same_fact": "true", "contradicts": false}', "end_turn", None, None),
                 ('{"same_fact": true}', "end_turn", None, None),
                 ("", "end_turn", RuntimeError("network"), None)]
        for text, stop, raises, want in cases:
            with self.subTest(text=text, stop=stop):
                sdk, made = self._fake_sdk(text, stop, raises)
                with mock.patch.dict(sys.modules, {"anthropic": sdk}):
                    before = llmcheck.calls
                    self.assertEqual(llmcheck._ask("m", "s", "q", "r"), want)
                    self.assertEqual(llmcheck.calls, before + 1)
                # a stalled network cannot hold ingest: bounded timeout, at most one retry
                self.assertLessEqual(made["client"].get("timeout", 600), 60)
                self.assertLessEqual(made["client"].get("max_retries", 2), 1)

    def test_missing_package_is_no_verdict_and_no_call(self):
        with mock.patch.dict(sys.modules, {"anthropic": None}):
            self.assertIsNone(llmcheck._ask("m", "s", "q", "r"))
        self.assertEqual(llmcheck.calls, 0)


if __name__ == "__main__":
    unittest.main()
