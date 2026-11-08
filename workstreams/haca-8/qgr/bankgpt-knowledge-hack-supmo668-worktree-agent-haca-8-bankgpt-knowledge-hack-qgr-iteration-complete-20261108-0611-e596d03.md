---
receipt_version: 2
type: qgr
boundary: iteration-complete
org: bankgpt-knowledge-hack
principal: supmo668
agent: worktree-agent
workstream: haca-8
project: bankgpt-knowledge-hack
diff_base: 9ffe90ce1e2135057ca7667d51711d0e6a44eabb
hash_a: a483e2b8af0d874614c824911034fd8bec9a58afe8d39d9b858712aaf1296a0c
hash_b: 3a188b047ed54bf0e1f70c188159b74bbc7b2cd3851d3d2bc18628444f255817
hash_c: 84c1418bca5aec4cb1726d40e40a173330819e70624f30fb648e5dd84aea73bd
hash_d: 84c1418bca5aec4cb1726d40e40a173330819e70624f30fb648e5dd84aea73bd
hash_d_source: "auto-approved — no principal 1B1"
hash_e: e596d031413b5b27e7854a9759f56204c56cbd02dbb923c28732a3d43f9495d1
date: 2026-11-08T06:11
---

# Receipt: iteration-complete — bankgpt-knowledge-hack

## Verifiable hashes (recomputed + matched by receipt-verify)

- A (original): a483e2b — artifact entering the gate
- E (final):    e596d03 — artifact after all fixes (verification anchor)

## Procedural attestation log (recorded, not independently verifiable)

These attest that each stage ran. Their inputs are ephemeral (review output,
triage notes, 1B1 transcripts) and cannot be reconstructed after the fact, so
they are a procedural log — NOT a cryptographic chain.

- B (findings):  3a188b0
- C (triage):    84c1418
- D (principal): 84c1418 — auto-approved — no principal 1B1

## Review Summary
HACA-8 iter 1.1: L2 deck (12 slides, PDF) + 5 SVG figures; 4-pass review, 5 findings kept, all fixed (baa8d96, 4a475f5, 1aa4f39); 57 tests green incl. snapshot leak check

## Review Summary
Four passes (code, test, design, security). Five findings kept, all fixed. Tests: 57 passed (python -m unittest discover -s tests, snapshot leak check on). PDF rebuild is byte-stable.

    HACA-8 iteration 1.1 QG findings (reviewers: code, test, design, security)
    TEST-1 score_progression regex borrows the next section's table when "Score per run" has none
    TEST-2 short expected answers (<6 words) never leak-checked
    TEST-3 positive control did not call the real leak check
    TEST-4 PDF freshness checked by page count only
    SEC-1 build_pdf.mjs embeds any path in an image href (path.join, no containment)
    TEST-1 fix baa8d96 (anchored ^##, stop at next heading; regression test)
    TEST-2 fix 4a475f5 (whole-phrase match for short answers; control over every short answer)
    TEST-3 fix 4a475f5 (eval_leaks/page_leaks shared by NoLeak and LeakCheckCatches)
    TEST-4 fix 4a475f5 (source-sha256 of deck.md+figures stamped into PDF Keywords, recomputed by test)
    SEC-1 fix 1aa4f39 (only figures/*.svg; verified throws on ../../README.md)
