# Data card

## Why synthetic data
No marketplace simulator, persona corpora or evaluation sets were supplied for this run of the assignment (the
starter kit contains a scaffold and five tiny examples). The instructor's guidance allows combining public datasets
with **clearly labelled synthetic data**; GigBid therefore generates everything from one seed with `datagen/build.py`.

## What is generated (`python -m datagen.build --seed 7`)

| Asset | Size | Where | Provenance |
|---|---|---|---|
| Personas (AI/ML engineer, full-stack developer, data analyst) | 3 | `data/personas/<persona>/` | synthetic |
| Projects per persona, each described **three ways** (case study, repo README + code, reviews/proposals) | 20–22 | `case_studies/`, `repos/` | synthetic |
| CV (Markdown + PDF) | 1 per persona | `cv.md`, `cv.pdf` | synthetic |
| Client reviews | ~12 per persona | `reviews.json` | synthetic |
| Past proposals labelled won / replied / ignored | 150 per persona | `proposals.jsonl` | synthetic; outcome drawn from a model where specific openings, cited evidence and fair prices raise reply odds |
| Clients with public history + hidden preferences | 320 | `gigsim/seed/clients.json`, `hidden_prefs.json` | synthetic |
| Jobs (dev 600, holdout 150, live stream 316 incl. ~6% duplicate reposts, scams, planted prompt injections) | 1,066 | `gigsim/seed/jobs.json` | synthetic bodies; titles and budget distributions from Kaggle when present |
| Market price statistics per category | 8 categories | `gigsim/seed/market_stats.json` | Kaggle when present, else log-normal synthetic |
| Gold labels (bid/skip, scam, injection, relevant evidence, fair price band) | per job × persona | `evals/datasets/{dev,holdout}/` | derived from the generator's ground truth |

Ground truth for retrieval lives in `data/personas/*/_manifest.json`, which the ingestion code never loads.

## Corpus difficulty (built to defeat naive RAG)
- **Implied skills**: a project's secondary skills appear only as paraphrases in the case study (e.g. "background task
  queue" for Celery) and only as imports in its code file.
- **Same project, three descriptions**: case study, README (40% are deliberately terse: "Internal tooling. See src/.")
  and reviews/proposals each describe it differently.
- **Outcome matters more than wording**: past-proposal outcomes depend on opening specificity, cited evidence and
  price, not on vocabulary.
- **Job posts** phrase 45% of requirements as paraphrases; 25% of in-category jobs add one off-domain requirement.

## Public datasets (optional inputs)
| Dataset | Licence | Used for | Status in this build |
|---|---|---|---|
| asaniczka — *Upwork Job Postings Dataset 2024 (50k)* (Kaggle) | ODC-By 1.0 (per Kaggle page — verify) | real job titles; fixed-budget and hourly-rate distributions per category | **not present** in `data/raw/` for the committed build (`evals/datasets/BUILD_SUMMARY.json`: `kaggle_used: false`); drop the CSV in `data/raw/` and rebuild to use it |
| asaniczka — *Freelance contracts 1.3M* | ODC-By 1.0 (verify) | contract value distributions | not used |
| Other datasets listed by the instructor | see Kaggle pages | — | not used |

When Kaggle data is used, every job carries `provenance: kaggle_title+synthetic_body`; otherwise `synthetic`.

## Limitations (read before trusting any number)
- **Scams and injections are template-based.** The Risk Assessor's rules reach precision/recall 1.0 on them; real
  scams are more varied, so treat those numbers as an upper bound. Paraphrased scams are the main gap (see the report).
- **The simulated client is a transparent scoring model** (`gigsim/app.py::score_proposal`): must-have coverage,
  metrics, personalisation, price, generic openings, policy violations. Reply-rate results measure fit to that model,
  not to real clients.
- **Gold "bid" labels are rule-derived** (coverage ≥ 0.6, budget, not a scam); a human freelancer might disagree on
  borderline jobs.
- Personas are fictional and labelled "(synthetic)"; no real person, company or client data is included. Live-mode
  data is never committed (`.gitignore: data/live/`).
