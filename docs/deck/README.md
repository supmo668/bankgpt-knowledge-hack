# L2 proof-of-work deck

- `deck.md`: the slides (markdown; GitHub renders it with the figures inline). Slides are split on `---`.
- `deck.pdf`: the same slides, one 16:9 page each.
- `figures/*.svg`: the charts and the architecture diagram.
- `data/scorer_output.json`: per-suite scores, pillars and per-question latencies from the kit scorer. It holds
  numbers only, with no question, answer or page text.

## Rebuild

```bash
python scripts/deck_measure.py --snapshots snapshots   # needs the unpacked kit snapshots (gitignored)
python scripts/deck_figures.py                         # SVGs from RESULTS.md, runs.csv and scorer_output.json
cd scripts/deck && npm ci && node build_pdf.mjs        # deck.md → deck.pdf (pure JS; no browser needed)
python -m unittest tests.test_deck                     # figures up to date, PDF page count, no leaked text
```

`tests/test_deck.py` fails if a figure is stale or if anything under `docs/deck` quotes a public eval question,
an expected answer or a site value. When the snapshots are unpacked (`snapshots/`, or `BANKGPT_SNAPSHOTS=<dir>`),
it also fails if the deck shares any 8-word run with snapshot page text.
