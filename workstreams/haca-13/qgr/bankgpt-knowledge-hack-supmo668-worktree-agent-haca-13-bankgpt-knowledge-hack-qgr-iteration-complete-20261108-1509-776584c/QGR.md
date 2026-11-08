# QGR — HACA-13 iteration 1.1 (iteration-complete)

Base `3af8206` (origin/main) · branch `feat/haca-13-pvr-followups` · receipt Hash E `776584c`.
Scope: R7 embed test, R8 branch-hours fact family, refusal diagnostics (unsupported-confident / wrongly-refused).

## Issues Found (post-scorer, threshold 80)

| ID | Score | Where | Issue | Disposition |
|---|---|---|---|---|
| C-1 | 95 | store.py row_key/row_values | lobby and drive-up tables on one page shared one hours fact; one shadowed | fixed: values per service (lobby / drive-up) from header or table heading |
| C-2 | 92 | store.py _branch_scope | "## Branch Hours" / index "## Downtown" headings collapsed branches | fixed: generic words dropped, nearest specific heading wins |
| C-3 | 85 | chunker.hours_cells, answer gate | Closed-only rows weren't hours facts; open→Closed missed | fixed: hours-ness per table; gate accepts Closed |
| C-6 | 80 | chunker.hours_cells | times compared as strings | fixed: `norm_hours` → H:MMam/pm |
| T-1 | 88 | conflicts.py | dedupe label untested | test: two branches, same change → 4 conflicts |
| T-2 | 85 | answer.py | double full-stop guard untested | test added |
| T-6 | 85 | test_branch_hours | "Closed" never asserted | test fixed |
| T-4 | 80 | test_branch_hours | vacuous cross-branch assertion | strengthened |
| T-10 | 80 | eval_report.py | defined-rate format + run_suite wiring untested | tests added |
| C-4/C-5 | 60 (fixed anyway) | chunker EFFECTIVE_RE | month alternation matched any word / hid a later M/D/YYYY | fixed: month names only, hours tables only |

Below threshold, not fixed: D-1 (60, runs.csv append vs deck — documented in RESULTS.md), D-2..D-6, T-7, T-8, T-9, T-11..T-13, S-1, S-2 (scorer reasons recorded in the gate log).

## Accountability
- reviewer-code: 6 · reviewer-security: 2 · reviewer-design: 7 · reviewer-test: 13 · reviewer-ux: skipped (no UI paths in diff; user_facing_paths unset)
- reviewer-scorer: scored 25, 9 passed threshold (>= 80)
- Own review: 0

## Bug-exposing tests
6 new tests in `HoursKeyingEdgeCases` were red before the fix (C-1, C-2 ×2, C-3, C-6, T-1) and are green after.

## Checks
| Check | Result |
|---|---|
| format | unset (skip) |
| lint (`compileall`) | pass |
| typecheck | unset (skip) |
| tests (`python3 -m unittest discover -s tests`) | 93 ran, OK, 0 failing |
| public eval (real snapshots, local only) | 0.7705, 0 stale-trap hits, 32 conflicts, gold recall 12/12 — unchanged |
| snapshot data tracked | none |

## Summary
R7: a separate host in a clean `python -I -S -B` interpreter embeds the solution through the contract; harness pinned to kit sha256 and run unchanged. R8: branch hours are facts (branch + day key, per-service values, effective dates), with all four draft cases plus keying edge cases tested. Diagnostics: public 0.222 (8/36) unsupported-confident, 0.000 (0/37) wrongly refused.

## Proposed Commit
Iteration 1.1 complete: HACA-13 draft-PVR follow-ups — R7 embed test, R8 branch-hours fact family, refusal diagnostics (QG: 9 findings fixed + C-4/C-5, 93 tests green, public 0.7705 unchanged)
