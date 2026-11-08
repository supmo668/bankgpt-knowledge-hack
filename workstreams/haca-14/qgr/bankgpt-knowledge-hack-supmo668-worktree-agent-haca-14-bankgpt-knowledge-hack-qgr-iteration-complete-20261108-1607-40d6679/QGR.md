# QGR — iteration-complete 1.1: validity intervals (HACA-14)

Base `b8d17d5` · Hash A `386d054` · Hash E `40d6679` · Hash D = C (iteration auto-approved)

## Issues Found
- reviewer-code: 6 · reviewer-security: 2 · reviewer-design: 6 · reviewer-test: 14 · reviewer-ux: skipped (not-user-facing)
- Own review: 0 new (cross-checked the answer/conflicts/solution diffs)
- reviewer-scorer: 15 consolidated, 5 passed threshold (>= 80)

| ID | Score | Finding | Disposition |
|---|---|---|---|
| F5 | 92 | dev-gap-03 gold mixed the cash-out and plain refinance tables after the caption-qualifier split | fixed: dev-gap-03 asks the cash-out refinance; new dev-gap-07 for the plain refinance |
| F1 | 85 | timeline backdated `valid_from` over crawls that lacked the fact; `facts_at` disagreed with `answer` | fixed: runs over every view, absent runs included; backdating only before the first view |
| F2 | 82 | an offer ending after the newest crawl was still quoted confidently | fixed: trailing views the day after each stated end date |
| F3 | 82 | ended offer could yield `valid_to` < `valid_from` | fixed: never opens a version |
| F7 | 80 | shadowed copies were one span per chunk | fixed: one version per contiguous stretch |
| F4 | 55 | future end date reported as "offer ended" | fixed (below threshold): "offer ends …, stated end date" |
| F11 | 65 | `__main__` guard before the last test class | fixed (below threshold) |
| F6, F8–F10, F12–F15 | 30–60 | perf at 1600 dated tables/gap, naming, input normalisation, docs | not actioned; F13/F15 documented in NOTES |

## Accountability
Bug-exposing tests: 6 in `tests/test_intervals.py`, red on the pre-fix store (6 failures), green after.
Coverage tests: 3 added (two dated tables in one gap, same-date snapshots, `facts_at` skips shadowed copies).

## Checks
| Check | Result |
|---|---|
| format | unset (skip) |
| lint (`compileall`) | pass |
| typecheck | unset (skip) |
| tests (`unittest discover`) | 113 OK, 0 failing |
| public eval | 0.7705 (unchanged), p95 0.03 s, ingest 4.7 s |
| dev eval | 1.0 |
| conflicts (own gold) | recall 12/12, precision 0.316 (was 0.375: the caption fix splits real Home Equity facts; iteration 2 targets precision) |
| data in tree | none tracked |

## Proposed Commit
Iteration 1.1 complete: validity intervals - views, fact timelines, trailing offer-end views, caption-qualifier keys (QG: 7 findings fixed, 113 tests, public 0.7705)
