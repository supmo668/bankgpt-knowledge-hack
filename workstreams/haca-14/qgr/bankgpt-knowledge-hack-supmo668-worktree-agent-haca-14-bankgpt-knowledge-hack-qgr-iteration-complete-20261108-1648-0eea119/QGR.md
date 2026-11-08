# QGR — iteration-complete 1.2: free-text facts + entity resolution (HACA-14)

Base `52e1fbb` · Hash A `b7afba7` · Hash E `0eea119` · Hash D = C (iteration auto-approved)

## Issues Found
- reviewer-code: 10 · reviewer-security: 3 · reviewer-design: 9 · reviewer-test: 13 · reviewer-ux: skipped (not-user-facing)
- reviewer-scorer: 21 consolidated, 3 passed threshold (>= 80); G18 not scored (requirement coverage)

| ID | Score | Finding | Disposition |
|---|---|---|---|
| G4 | 92 | yes/no lead: exact float over any column/retrieved rows; wrong "No" on comparisons, ranges, partial tiers | fixed: asked column, whole table, ranges count, no lead on comparisons |
| G3 | 82 | gold matched on the system's own "paragraph vs table" label; cg-17..19 indistinguishable | fixed: site values only; Achieve cases dropped as false positives |
| G6 | 80 | back-reference regex accepted any "The …" sentence | fixed: referring noun required |
| G2 | 58 | tests/comments copied public questions | fixed: made-up products, organisations and phrasing |
| G5, G7, G8, G9, G10, G11, G12, G13, G14, G15, G17, G20 | 30–74 | view-blind continuation, amount/tier/term/date scoping, intro per column, range agreement, value boundary, caches, regex length cap, hand-listed award orgs, docstring | fixed (below threshold) |
| G18 | — | fees and opening hours in paragraphs not checked against tables | deferred, documented in NOTES ("Not covered yet") |
| G1, G16, G19, G21 | 20–45 | latent/style/coverage | G21 coverage added; others not actioned |

## Accountability
Rewritten `tests/test_textfacts.py` (31 tests): 15 fail on the pre-fix code, all pass after.
QG outcome on data: the resolver tightening removed 3 false Achieve conflicts; 4 genuine paragraph-vs-table
conflicts remain, which is below the issue's "at least 6" target (no further genuine cases exist in the snapshots).

## Checks
| Check | Result |
|---|---|
| lint (`compileall`) | pass |
| tests | 144 OK, 0 failing |
| public eval | 0.8403 (v1 0.7705), p95 0.04 s, ingest 5.9 s |
| dev / blind | 1.000 / 0.891 |
| own conflicts gold | 16/16, precision 0.381, resolution 0.9375 |
| data in tree | none tracked |

## Proposed Commit
Iteration 1.2 complete: free-text rate facts resolved to table keys, paragraph-vs-table conflicts, award / back-reference / value-check answers (QG: 17 findings fixed, 144 tests, public 0.8403)
