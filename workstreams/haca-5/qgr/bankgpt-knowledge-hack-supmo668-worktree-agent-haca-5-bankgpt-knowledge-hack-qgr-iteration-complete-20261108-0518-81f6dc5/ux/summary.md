# UX review — feat/haca-5-knowledge-solution @ e4c43f9
reviewed: head e4c43f9f25bd670adeef334739ca93d0a7a0d430 tree 697a979903e10475fec17bac493a38fba22f737e
ux verdict: user-facing (default globs (user_facing_paths unset); base 7e03dd673ddb84898bfdcfbf10289d2fd48b83f1)
config: none in repo — scratch `.ux-smoke/ux.config.ts` (webServer `python3 demo/app.py --port 8799 --snapshots <worktree>/snapshots`, throwaway localhost instance)   viewports: 1280x800, 390x844
trace: trace.zip sha256 24e4e7a4a1752b39617d0e836ccacec019de4fe3410f280636885849428a2747 (first failing test, A6 mobile; kept in UX_DIR, not attached)
runner note: the cached chromium headless shell (build 1248) is missing system libs on this box (libglib, nss, X11, ...; no root). Debian trixie arm64 libs and DejaVu fonts were extracted to a scratch dir and passed through LD_LIBRARY_PATH/FONTCONFIG_FILE. The repo was not modified.
acceptance: lines supplied by caller (gate brief), not on issue

| # | Acceptance (verbatim) | Spec | Result | Screenshots |
|---|---|---|---|---|
| 1 | `/` loads with a question box, an as_of select listing "latest" plus 5 snapshot dates, and an Ask button. | smoke-01.spec.ts | pass (both viewports; no console errors) | 01-home-form-desktop.png, 01-home-form-mobile.png |
| 2 | Asking "What's the APR for a 60-month auto loan on a 2025 model-year vehicle?" shows an answer containing 5.39%, a "Why this value" note, and at least one source link. | smoke-02.spec.ts | pass (confident; 3 source links) | 02-auto-apr-desktop.png, 02-auto-apr-mobile.png |
| 3 | Asking "What is the rate on a 7/1 ARM?" shows a declined (refused) card with no digits in the answer text. | smoke-03.spec.ts | pass ("I don't know: that isn't listed on the website. Please contact us and a team member can help.") | 03-arm-refusal-desktop.png, 03-arm-refusal-mobile.png |
| 4 | Choosing as_of 2026-05-19 and asking "What was the 10-month jumbo certificate special APY?" shows 3.90%. | smoke-04.spec.ts | pass (select keeps 2026-05-19 after submit) | 04-jumbo-asof-desktop.png, 04-jumbo-asof-mobile.png |
| 5 | `/conflicts` shows a table with Topic/Values seen/Resolution/Why columns and >= 10 rows. | smoke-05.spec.ts | pass (32 rows) | 05-conflicts-desktop.png, 05-conflicts-mobile.png |
| 6 | Content does not overflow horizontally at 390px (or note it), basic accessibility (input has a label or placeholder, page has lang). | smoke-06.spec.ts | desktop pass; mobile FAIL on overflow (lang=en and placeholder present: pass) | 06-overflow-a11y-desktop.png, 06-overflow-a11y-mobile.png |

Existing specs run: none (repo has no Playwright specs) — 0/0
Smoke result: 11/12 passed (A6 @mobile failed)

## Findings: 4

1. **[HIGH]** `/` and `/?q=…` @ 390x844: the page scrolls horizontally. scrollWidth is 414 against a 390 viewport. The as_of `<select>` is sized to its longest option ("as of 2026-05-12 (01_2026-05-12_baseline)") and renders 398px wide, past the 358px content box. Source URLs in the answer card also run past the card edge (right=398).
   - Evidence: 01-home-form-mobile.png, 02-auto-apr-mobile.png, smoke-06.spec.ts:29
   - Recommendation: add `select { max-width: 100%; min-width: 0; }` (or `flex: 1 1 100%` on mobile) and `ul.src a { overflow-wrap: anywhere; }`; optionally shorten option text to the date only.
2. **[HIGH]** `/conflicts` @ 390x844: the table is 472px wide inside a 356px card, and the page scrollWidth is 505. Every column is crushed to a few characters per line, so the page is about 11.5k px tall and hard to read.
   - Evidence: 05-conflicts-mobile.png, smoke-06.spec.ts:29
   - Recommendation: wrap the table in `<div style="overflow-x:auto">` and add `td { overflow-wrap: anywhere; }`. Alternatively, stack rows as cards below about 600px with a media query.
3. **[LOW]** `/` @ both: the question input has only a placeholder and no `<label>` or `aria-label`, and the as_of `<select>` has no accessible name at all. This passes the acceptance bar (placeholder), but screen readers announce the select as an unnamed combobox.
   - Evidence: 01-home-form-desktop.png, smoke-06.spec.ts (A6 log: inputLabel.labels=0, selectLabel.labels=0, aria=null)
   - Recommendation: add `aria-label="Question"` to the input and `aria-label="Answer as of snapshot"` to the select (or visually-hidden `<label>`s).
4. **[INFO]** repo: there is no Playwright config or e2e specs, so this pass ran on a scratch config that boots the demo itself. Acceptable for a stdlib demo. Add a config only if the demo is meant to stay a gated UI.
   - Evidence: smoke run config `.ux-smoke/ux.config.ts` (deleted after run)
   - Recommendation: none required for HACA-5.
