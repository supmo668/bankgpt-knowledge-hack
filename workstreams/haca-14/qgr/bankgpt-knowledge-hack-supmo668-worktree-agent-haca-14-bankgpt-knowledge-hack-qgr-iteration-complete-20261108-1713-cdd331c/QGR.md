# QGR — HACA-14 iteration 1.3: optional LLM contradiction check

Base `2b145b7` → HEAD `9c82c61`. Boundary: iteration-complete (auto-approved). Receipt Hash E `cdd331c`.

## Stage 1 — review
- reviewer-code: 8   - reviewer-security: 3   - reviewer-design: 9
- reviewer-test: 12  - reviewer-ux: skipped (not-user-facing)
- reviewer-scorer: scored 14 consolidated, 3 passed threshold (>= 80)
- Own review: 1 (cache shape; merged into F3). The API request shape was checked against the official Claude API reference; it is valid (F13 rejected).

## Issues found and disposition
| ID | Score | Finding | Disposition |
|---|---|---|---|
| F1 | 85 | `_judged` checked agreement one tier at a time: a tier-less claim matching one tier was judged against another → false conflict | fixed `e96b859` |
| F2 | 80 | `undecided` matched repeated disclosures through their heading, which the rules reject | fixed `e96b859` |
| F12 | 80 | test gaps (verdict combos, exact flag, reply parsing, weak call count, on-disk check) | fixed `f350af3` |
| F3 | 75 | cache file/entry of the wrong shape crashed ingest | fixed `3587fa2` |
| F8 | 70 | `calls` comment named readers that did not exist | fixed `3587fa2` |
| F5 | 65 | no client timeout / retries bound | fixed `3587fa2` (30 s, 1 retry) |
| F4 | 60 | RESULTS flag-on row mixed measurements from two commits | fixed `9c82c61` (re-measured) |
| F9, F6, F14, F7, F10, F13 | 50–20 | reason-note edge case, non-atomic write, style, key scope, prompt delimiting, live call | rejected (rationale in triage) |

## Red → green
`f350af3` added 8 tests; 15 sub-cases failed before the fixes (F1, F2, F3, F5), all green after.

## Checks (run this turn)
- format: unset · typecheck: unset
- lint: `python3 -m compileall -q submission tests scripts demo` → exit 0
- tests: `python3 -m unittest discover -s tests` → 158 OK, 0 failing
- public eval, flag off: 0.8403 (current_value 0.67, conflict_resolution 0.67, should_refuse 3/3, min category 0.50); own gold 16/16, precision 0.381, resolution 0.94; p95 0.03 s; ingest 5.4 s
- public eval, flag on: 0.8403; 1 undecided pair, 0 requests (package absent), no verdict; ingest 5.8 s
- data: no `snapshots*`, page text or `.llm_cache.json` tracked

## Proposed commit
Iteration 1.3 complete: optional LLM contradiction check behind BANKGPT_LLM_CHECK=1 (QG: 7 findings fixed, 158 tests, public 0.8403 flag off and on)
