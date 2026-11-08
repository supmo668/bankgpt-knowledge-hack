---
type: session
trigger: iteration-complete
agent: bankgpt-knowledge-hack/supmo668/worktree-agent
issue: HACA-5
branch: feat/haca-5-knowledge-solution
boundary_commit: 4b7ca05
date: 2026-11-08
---

# Handoff: HACA-5 iteration 1.1 complete (bank-gpt knowledge solution v1)

## State
- Branch `feat/haca-5-knowledge-solution` is pushed. The tree is clean. The boundary commit `4b7ca05` carries the QGR receipt
  `workstreams/haca-5/qgr/...-iteration-complete-20261108-0518-81f6dc5.md`, with QGR.md and ux/ evidence next to it.
- Worktree: `worktrees/haca-5` (gitignored dir in the shared checkout).
- Submission: `submission/solution.py` + `submission/bankgpt/`, deterministic, stdlib only, no keys.

## Done-when vs evidence
- Public eval ≥ 0.60: **0.7705** (baseline 0.093). No category at 0. should_refuse 3/3. 0 stale-trap hits.
- Answer p95 ≤ 2 s: 0.04–0.06 s. Ingest of all five snapshots ≈ 7–10 s.
- Fresh clone passes: cloned from GitHub, kit zips dropped into `snapshots_zip/`, clean Python 3.11.10 → 0.7705; 37 unit tests OK.
- Data not in git history: no zip or snapshot page blob in `7e03dd6..HEAD`; `snapshots_zip/`, `/snapshots/`, `*.zip` gitignored.
- Proof of work: `docs/eval/RESULTS.md` (score per run, per category), `latest.md`/`runs.csv`
  (`scripts/eval_report.py`), our own `dev_eval.jsonl`, `dev2_blind.jsonl` (first run 0.80, now 0.89) and `conflicts_gold.json`;
  the demo is `python demo/app.py`.

## Decisions taken (mine)
- `agency.yaml`: set `quality.test_command` (`python3 -m unittest discover -s tests`) and `quality.lint_command`
  (compileall), because the gate STOPs on an unset test command. CTO: keep or move.
- Currency-rule order: effective date ranks above authority (NOTES rule 2). The design reviewer read PVR R3 as
  authority first. I kept date first: on the real data, an official page updated later is newer information, and
  calculators and blogs are barred from rate answers anyway. Flag for the CTO if R3 should be read strictly.

## Known limits / next
- Phase 2 (LLM behind a flag) would cover the public misses that need synthesis across passages (dup-049/081/060/031/094/087/008).
- Conflict precision against an unknown gold: we submit 32. On our own gold, recall is 12/12 and precision 0.375.
- Next action: CTO review / landing (`/pr-cto-land`). I do not land.
