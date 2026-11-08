# NOTES: bank-gpt knowledge system

**Submission folder:** `submission/` (`solution.py` with `build()`, plus the `bankgpt/` package next to it).

```bash
python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl
```

**API keys: none.** v1 is fully deterministic: standard library only, no network, no model calls.
`requirements.txt` is intentionally empty. It runs on a clean Python 3.11 (checked with 3.11.10) and 3.13.

## Getting the data (not in this repo)

The snapshots are a real credit union's website, shared for the event only, and this repo is public. So
`snapshots_zip/` and `snapshots/` are gitignored and were never committed. To run anything:

1. Copy the five kit zips into `snapshots_zip/` at the repo root:
   `01_2026-05-12_baseline.zip` … `05_2026-09-28_latest.zip`.
2. Run the scorer once. It unpacks them into `snapshots/` (or unzip them there yourself).

The kit's synthetic selftest site (`harness/selftest/`) is in the repo and needs no data.

## How it works

**Ingest (about 7 s for all five snapshots).** Every page of every snapshot is cut into chunks: table rows
(with their caption, section and header product name as context), `label: value` lines, label groups such as
"**Lobby** Mon–Fri 8:30–5", paragraphs, and the sentences of long paragraphs. Each chunk is stored once, with a
bitmask of the snapshots that crawled it. A newer crawl never overwrites an older one (R1). Each page gets a
source kind and an authority, plus a publication date when it is a press release or blog post. A BM25 index
covers chunk text + context + page title.

**Facts and conflicts at ingest.** Every table row and `label: value` line gets a *fact key*: product scope +
its qualifier cells (term, balance or loan-amount tier, model-year tier, rate vs APY vs APR column). Model-year
ranges are aliased by their rank in the table, so "2024-27" on the rate sheet and "2023-26" on a stale page are
the same fact (the newest tier). Inside each snapshot view, rows sharing a key but disagreeing on a shared value
column are resolved by the currency rules below. Losers are *shadowed*: never quoted, never a source.
`conflicts()` returns these groups (stale pages and cross-snapshot changes, one entry per fact, identical
mirrored changes merged), plus dated text facts (branch counts in press-release boilerplate, "Nth time"
recognitions). The resolution is the value the answer path gives today.

**Answer (p95 ≈ 0.05 s).**
1. Pick the view: `as_of=None` → the latest snapshot; `as_of=D` → the newest snapshot taken on or before D. An
   "as of May 2026" phrase in the question text works too.
2. Detect intent: rate, fee, hours, count, link, list, address, superlative, descriptive.
3. Rank with BM25 × authority × recency × qualifier fit: the term, amount and model year asked for must match the
   row's tier, a qualifier the question didn't ask for (jumbo, business, IRA, intro, refinance…) is penalised, and
   page-title/heading matches are boosted.
4. Gate: a rate answer needs a real rate value from a rate sheet or product page, the product named, the
   term/amount/qualifier asked, and an offer that hasn't ended. Hours need a time, counts a number next to the
   counted noun. Proper names in the question (Galena, Hy-Vee, Mullan Avenue) must appear in the passage.
5. Compose one value per fact: the matching row(s) of one table (all tiers only when no tier was named), or the
   best one or two sentences. Superseded values never appear in `text`. The reason a value won goes in
   `conflict_note`.
6. Refuse honestly (`confident=False`, no digits in the text) when nothing passes the gates, or the best passage
   covers under 25% of the question's information (IDF mass).

Sources are the answering page plus the other authoritative pages that carry the same value.

## Currency rules (what "current" means)

Unit-tested in `tests/test_currency_rules.py` (decision D3).

1. **Snapshot view.** "Current" means the newest snapshot. A page missing from a later crawl is carried forward
   from its last crawl (penalised ×0.6 when ranking), because a crawl gap is not a deletion.
2. **Newest effective date wins.** Rate tables carry "Rates are effective as of MM/DD/YYYY". For the same fact
   key, the row with the newest effective date wins. Undated copies lose to dated ones.
3. **Then source authority.** Rate sheet (`/rates`, `/business-rates`) 1.0 > product pages 0.9 > landing pages
   0.7 > newsroom 0.6 = PDF documents 0.6 > blog 0.45 > resources 0.4 > calculators 0.25 > tag/author indexes 0.2
   > expired-offer pages 0.1. Calculators and blogs never answer a rate question.
4. **Expired offers never win.** Text such as "offer ended 2025-09-30" or "valid through December 31, 2025" before
   the snapshot date marks the chunk expired: excluded from rate/fee answers, last in conflict resolution.
5. **Dated statements: the newest publication wins.** Counts and "Nth time" claims in press releases are taken
   from the most recently published release, so "22 branch office locations" (2026) beats "24" (older boilerplate),
   and "fifth time" beats "fourth time".
6. **Qualifiers are different facts, never conflicts.** Term, rate vs APY vs APR, standard vs jumbo vs special,
   balance or loan-amount tier, personal vs business, model year, purchase vs refinance.
7. **When the site doesn't say, say so.** No guessing across terms or tiers. "It isn't listed; please contact
   us" with no numbers.

## Proof of work

- `docs/eval/RESULTS.md`: score per run from the 0.093 baseline, per-category scores, the dev/blind/selftest
  suites (with an honest note on which were tuned against), and conflicts.
- `docs/eval/latest.md` and `runs.csv`: regenerated by `python scripts/eval_report.py`.
- `docs/eval/dev_eval.jsonl`, `dev2_blind.jsonl` and `conflicts_gold.json`: our own as-of, reworded and
  refusal items, plus gold conflicts, built from snapshot diffs.
- `demo/app.py`: a local UI (question → answer, sources, conflict note, `as_of` picker, conflicts table).
  Run `python demo/app.py` and open http://127.0.0.1:8765. Standard library only; it binds to localhost.
- `tests/`: `python -m unittest discover -s tests` (synthetic pages; no snapshot data needed).

## Phase 2 (not in v1)

An LLM answer-composer for the questions lexical retrieval can't settle (cross-passage synthesis such as "no
tier pays 0.35%"). The plan: opt-in behind an environment flag, temperature 0, answers cached for consistency,
and adopted only if it raises the public score without breaking determinism or the 2 s p95. It would need
`ANTHROPIC_API_KEY`. v1 needs no key.
