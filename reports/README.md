# Reports

Everything in this folder except `approach.md` and `prompt_changelog.md` is **generated** by the
evaluation harness from real runs. Nothing is written by hand and nothing is shipped pre-filled.

| File | Produced by |
|---|---|
| `evaluation.md` / `.csv` / `.json` | `make eval` (labelled documents vs. ground truth) |
| `failure_analysis.md` | `make eval` |
| `cost_analysis.md` | `make eval` (labelled set) |
| `cost_analysis_all.md` | `make extract-all` / `make cost-report` (all documents) |
| `experiments/<tag>/…` + `experiments/index.json` | `make eval-baseline`, `make eval-experiment …` |
| `synthetic/…` | `make eval-synthetic` — harness demo on generated fixtures, **not** assignment results |

The web app's Evaluation page reads `evaluation.json` from this folder.
