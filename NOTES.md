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
its qualifier cells (term, balance or loan-amount tier, model-year tier, rate vs APY vs APR column; opening-hours rows
use branch + day, see "Extending" below). Model-year
ranges are aliased by their rank in the table, so "2024-27" on the rate sheet and "2023-26" on a stale page are
the same fact (the newest tier). Inside each snapshot view, rows sharing a key but disagreeing on a shared value
column are resolved by the currency rules below. Losers are *shadowed*: never quoted, never a source.
`conflicts()` returns these groups (stale pages and cross-snapshot changes, one entry per fact, identical
mirrored changes merged), plus dated text facts (branch counts in press-release boilerplate, "Nth time"
recognitions). The resolution is the value the answer path gives today.

**Answer (p95 ≈ 0.05 s).**
1. Pick the view: `as_of=None` → the latest snapshot; `as_of=D` → the view in force on D (the newest crawl or
   interval view dated on or before D, see "Validity intervals" below). An "as of May 2026" phrase in the
   question text works too.
2. Detect intent: rate, fee, hours, count, link, list, address, superlative, descriptive.
3. Rank with BM25 × authority × recency × qualifier fit: the term, amount and model year asked for must match the
   row's tier, a qualifier the question didn't ask for (jumbo, business, IRA, intro, refinance…) is penalised, and
   page-title/heading matches are boosted.
4. Gate: a rate answer needs a real rate value from a rate sheet or product page, the product named, the
   term/amount/qualifier asked, and an offer that hasn't ended. Hours need a time, counts a number next to the
   counted noun. Proper names in the question (Galena, Hy-Vee, Mullan Avenue) must appear in the passage.
5. Compose one value per fact: the matching row(s) of one table (all tiers only when no tier was named), or the
   best one or two sentences. Superseded *table and `label: value`* facts are shadowed and never quoted.
   Free-text paragraphs are not fact-checked against the tables; they are kept out of rate answers by the gates
   in step 4 (rate sheet / product / landing / home pages only, real rate value, named product). That is a
   known limit. The reason a value won goes in `conflict_note`.
6. Refuse honestly (`confident=False`, no digits in the text) when nothing passes the gates, or the best passage
   covers under 25% of the question's information (IDF mass).

Sources are the answering page plus the other authoritative pages that carry the same value.

## Currency rules (what "current" means)

Unit-tested in `tests/test_currency_rules.py` (decision D3).

1. **Snapshot view.** "Current" means the newest snapshot. A page missing from a later crawl is carried forward
   from its last crawl (penalised ×0.6 when ranking), because a crawl gap is not a deletion.
2. **Newest effective date wins.** Rate tables carry "Rates are effective as of MM/DD/YYYY", and the date is
   recorded per crawl. For the same fact key, the row with the newest effective date wins. Undated copies lose to
   dated ones. This deliberately puts the date ahead of authority: a product page updated after the rate sheet
   is newer information.
3. **Then source authority.** Rate sheet (`/rates`, `/business-rates`) 1.0 > product pages 0.9 > home page 0.85 >
   landing pages 0.7 > newsroom 0.6 = PDF documents 0.6 > blog 0.45 > resources 0.4 > calculators 0.25 > tag/author
   indexes 0.2 > expired-offer pages 0.1 > modal/query-string duplicates 0.05. Calculators and blogs never answer a
   rate question (the rate gate admits only rate sheet, product, landing and home pages).

   The exact tie-break order for two rows of one fact (`Store.claim`): offer still live → newest effective date →
   page present in this crawl (not carried forward) → authority → most recent publication date → the value that
   appeared in the latest crawl.
4. **Expired offers never win.** Text such as "offer ended 2025-09-30" or "valid through December 31, 2025" before
   the snapshot date marks the chunk expired: excluded from rate/fee answers, last in conflict resolution.
5. **Dated statements: the newest publication wins.** Counts and "Nth time" claims in press releases are taken
   from the most recently published release, so "22 branch office locations" (2026) beats "24" (older boilerplate),
   and "fifth time" beats "fourth time".
6. **Qualifiers are different facts, never conflicts.** Term, rate vs APY vs APR, standard vs jumbo vs special,
   balance or loan-amount tier, personal vs business, model year, purchase vs refinance.
7. **When the site doesn't say, say so.** No guessing across terms or tiers. "It isn't listed; please contact
   us" with no numbers.

## Validity intervals (v2)

A crawl only says what the site showed on the day it was taken. Pages say more: "Rates are effective as of
09/25/2026", "offer valid through June 30, 2026". v2 turns both into time-exact intervals.

- **Views.** Every crawl is a view dated on its `taken_on`. Between two crawls A and B, each table of B whose
  version differs from A and whose stated effective date E falls strictly inside the gap (A < E < B) opens an
  *interval view* on E: A's content with that table's B version swapped in. An offer visible in A whose end date
  passes inside the gap opens a view the day after it ends (the offer closes there). `answer(q, as_of=D)` uses the
  newest view dated on or before D, so an `as_of` between crawls lands on the stated effective date, not on the
  nearest crawl.
- **Fact timelines.** Each fact key has versions with `valid_from` / `valid_to` (`Store.timeline(key)`,
  `Store.facts_at(as_of)`). `valid_from` is the stated effective date when the page gives one and no earlier crawl
  contradicts it, else the date of the view where the value first appeared. A version closes when a newer value of
  the same key takes over (`closed_reason` = "replaced by … (effective …)"), when its offer ends ("offer ended
  …"), or when the fact disappears from the site.
- **Shadowed values stay stored.** A losing copy (a stale calculator or landing page) is kept as a version with
  `shadowed=True`, its own interval, and the currency rule that beat it ("shadowed: loses to /rates on newer
  effective date …"). It is never quoted.
- **Observation beats a backdated claim.** If B says "effective 05/01" but crawl A, taken 05/12, still showed the
  old value, the switch stays at B's crawl date: we saw the old value live on a day the new one claims to cover.
- **Undated changes** switch at the crawl that first shows them (there is no better date to use).
- **After the newest crawl**, an offer it still shows stops being quoted the day after its stated end date
  (a *trailing view*); while it runs, its version carries that scheduled end ("offer ends …, stated end date").
- **Before the first crawl**, `answer` falls back to the first crawl (so a question about an earlier date still
  gets the oldest known value), while `facts_at` is exact and returns only facts whose interval has begun.
- **Conflicts come from observed crawls only.** Interval and trailing views are inferred states, so they shape
  answers and timelines but never add entries to `conflicts()`.
- Snapshots are expected in date order, as the harness delivers them (`run_eval.py` sorts by `taken_on`).

Tested on synthetic pages in `tests/test_intervals.py` (effective date inside a gap, backdated date vs
observation, undated change, offer ending mid-gap, shadowed copies stored). `docs/eval/dev_eval.jsonl` carries
`dev-gap-*` items whose `as_of` falls between snapshots, built from snapshot diffs.

## Embedding it in another Python app (R7)

The contract is the whole interface: load `submission/solution.py`, call `build()`, then `ingest(snapshot)` once per
snapshot (oldest first), then `answer(question, as_of=None)` and `conflicts()`. Snapshots and pages can be any
objects with the contract's attribute names (`snapshot_id`, `taken_on`, `pages`; `url`, `title`, `markdown`,
`fetched_at`). The returned `Answer` / `Conflict` / `Source` are the kit's `harness/contract.py` dataclasses: the
solution imports them from `harness/` when it sits next to the submission folder (the scorer's layout).
`tests/test_embeddable.py` proves this. A throwaway host script in a temp directory, run as `python -I -S -B`
with an empty environment, builds two independent instances from its own duck-typed snapshots (the kit's
selftest site) and checks the answers, `as_of`, refusals, conflicts and that no non-stdlib module was loaded. The
same test runs the organiser's `harness/run_eval.py` unchanged in the same clean interpreter (selftest 1.0) and
pins the three harness files to the kit's sha256.

## Extending: a new fact family (R8)

Scenario: branch hours with a location and an effective date (synthetic site in `tests/test_branch_hours.py`; no
public question is hard-coded). Cases covered: the current answer, a historical change (`as_of` and "in February
2026"), a different branch, missing evidence (a branch page with no hours, an unknown branch), and a stale index page.

What already worked unchanged: the answer path (hours intent, time gate, proper-name gate, `as_of` views,
refusal). Those cases pass on the pre-change code too. What did not: hours were not *facts*. The times were read
as qualifiers, so a change of hours never formed a fact group, nothing was shadowed and `conflicts()` stayed
silent. The month-name effective date ("effective April 6, 2026") was also ignored.

Files changed for the family (about 110 lines, no new module and no plugin layer):

- `submission/bankgpt/chunker.py`: `hours_cells()` recognises an hours row: a time-range cell with a.m./p.m. or
  noon, or "Closed" in a table whose other rows carry times. `norm_hours()` writes every time as `H:MMam/pm`, so
  "9 a.m.-5 p.m." and "9:00 AM to 5:00 PM" are one value. Month-name effective dates ("effective April 6, 2026")
  are read for hours tables only, so rate tables keep their M/D/YYYY rule.
- `submission/bankgpt/store.py`: an hours row's fact key is `hours | <branch> | <day>`. The branch is the nearest
  heading that names one ("Main Street Branch"), or else the nearest specific heading ("## Downtown" on an index
  page). Generic words (branch, location, hours, lobby, drive-up…) are dropped, so the same branch on
  `/locations` and on its own page is one fact, a "## Branch Hours" heading names no branch, and two branches are
  never a conflict. The values are the times per service (lobby or drive-up, taken from the column header or else
  the table's heading), so separate lobby and drive-up tables on one page are separate facts.
- `submission/bankgpt/conflicts.py`: hours values and topics are formatted as times ("main street hours:
  saturday"), with the branch kept in the dedupe label.
- `submission/bankgpt/answer.py`: the hours gate also accepts "Closed" from an hours table, and a one-line fix
  for a doubled full stop after "p.m.".
- `tests/test_branch_hours.py`: the scenario plus the keying edge cases found at the quality gate.

The currency rules apply as written: the newest effective date wins, then the page that is present, then
authority. Public score, conflicts and every existing test are unchanged (0.7705, 32 conflicts, 12/12 gold recall).

## Proof of work

- `docs/eval/RESULTS.md`: score per run from the 0.093 baseline, per-category scores, the dev/blind/selftest
  suites (with an honest note on which were tuned against), the refusal diagnostics (unsupported-confident and
  wrongly-refused rates) and conflicts.
- `docs/eval/latest.md` and `runs.csv`: regenerated by `python scripts/eval_report.py`.
- `docs/eval/dev_eval.jsonl`, `dev2_blind.jsonl` and `conflicts_gold.json`: our own as-of, reworded and
  refusal items, plus gold conflicts, built from snapshot diffs.
- `docs/deck/`: the L2 deck (`deck.md`, `deck.pdf`) and its figures, rebuilt from the files above (see its README).
- `demo/app.py`: a local UI (question → answer, sources, conflict note, `as_of` picker, conflicts table).
  Run `python demo/app.py` and open http://127.0.0.1:8765. Standard library only; it binds to localhost.
- `tests/`: `python -m unittest discover -s tests` (synthetic pages; no snapshot data needed).

## Phase 2 (not in v1)

An LLM answer-composer for the questions lexical retrieval can't settle (cross-passage synthesis such as "no
tier pays 0.35%"). The plan: opt-in behind an environment flag, temperature 0, answers cached for consistency,
and adopted only if it raises the public score without breaking determinism or the 2 s p95. It would need
`ANTHROPIC_API_KEY`. v1 needs no key.
