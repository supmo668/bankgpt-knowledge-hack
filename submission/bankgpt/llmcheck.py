"""Optional LLM contradiction check (v2, NOTES.md "Optional LLM contradiction check"). Off by default.

Only the free-text pairs the deterministic resolver could not settle reach it (`TextFacts.undecided`: a paragraph
value whose product the sentence names too weakly for the rules). For each pair it asks one question: is this
sentence about this table fact, and does it state a different value? The answer is cached by a hash of the
input, so repeat runs give identical conflicts and answers, and a cached run needs no network.

    BANKGPT_LLM_CHECK=1          turn it on (anything else, or unset: off; no call is ever made)
    ANTHROPIC_API_KEY            credential for the call (read by the SDK; only needed for uncached pairs)
    BANKGPT_LLM_MODEL            model id (default claude-opus-5-5)
    BANKGPT_LLM_CACHE            cache file (default: .llm_cache.json next to the submission, git-ignored)

Any failure (package not installed, no key, network, refusal, unparseable reply) is "no verdict": the pair stays
undecided and the deterministic result stands.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Optional

PROMPT_VERSION = "v1"
DEFAULT_MODEL = "claude-opus-5-5"
SYSTEM = ("You check a bank website for self-contradictions. You get one sentence from a page and one row from a "
          "rate table on the same site, taken from the same crawl. Decide two things: same_fact - is the sentence "
          "stating the value of the very product, tier and term this row describes (not a different product, an "
          "example for another product, a bound, or a hypothetical)? contradicts - if it is the same fact, does the "
          "sentence give a different value than the row for the quoted value? Reply with JSON only: "
          '{"same_fact": true|false, "contradicts": true|false}.')

_cache: Optional[dict] = None
calls = 0          # live API calls made in this process (tests and the eval report read it)


def enabled() -> bool:
    return os.environ.get("BANKGPT_LLM_CHECK") == "1"


def _cache_path() -> Path:
    return Path(os.environ.get("BANKGPT_LLM_CACHE") or Path(__file__).resolve().parents[1] / ".llm_cache.json")


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_cache_path().read_text())
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save() -> None:
    try:
        p = _cache_path()
        p.write_text(json.dumps(_cache, sort_keys=True, indent=0))
    except OSError:
        pass   # a read-only checkout still works; answers just aren't persisted


def cache_key(model: str, sentence: str, quote: str, row: str) -> str:
    blob = json.dumps({"v": PROMPT_VERSION, "model": model, "sentence": sentence, "quote": quote, "row": row},
                      sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def _ask(model: str, sentence: str, quote: str, row: str) -> Optional[dict]:
    """One Messages API call. Returns the parsed verdict or None."""
    global calls
    try:
        import anthropic   # optional dependency: only imported when the flag is on and the pair is uncached
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic()
        calls += 1
        resp = client.beta.messages.create(
            model=model, max_tokens=1024, system=SYSTEM,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            messages=[{"role": "user", "content": f"Sentence: {sentence}\nQuoted value: {quote}\nTable row: {row}"}],
        )
    except Exception:   # noqa: BLE001 - any API or network failure means "no verdict", never a crash
        return None
    if resp.stop_reason == "refusal":
        return None
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{[^{}]*\}", text)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(v.get("same_fact"), bool) or not isinstance(v.get("contradicts"), bool):
        return None
    return {"same_fact": v["same_fact"], "contradicts": v["contradicts"]}


def judge(sentence: str, quote: str, row: str) -> Optional[dict]:
    """The cached verdict for one (sentence, value, table row) pair; None when off or undecidable."""
    if not enabled():
        return None
    model = os.environ.get("BANKGPT_LLM_MODEL") or DEFAULT_MODEL
    key = cache_key(model, sentence, quote, row)
    cache = _load()
    if key in cache:
        return cache[key]
    v = _ask(model, sentence, quote, row)
    if v is not None:          # only real verdicts are cached; a failure is retried on the next run
        cache[key] = v
        _save()
    return v
