# Signature extension — X2: proposal experiments

**What:** two proposal strategies run as an A/B test against GigSim's simulated clients, with a significance test on
reply rate, so writing changes are decided by measured outcomes rather than taste.

| Strategy | What the writer does |
|---|---|
| `evidence_first` (A, default) | restate the client's core need, cite up to **three** case studies with their measured results, offer a plan for any gap, close with a first-week question |
| `concise_question` (B) | restate the need, cite the **one** strongest case study, close with a scoping question about priorities |

**How it is measured** (`evals/runners/ab_test.py`): every gold-"bid" dev job (up to 60 per persona) is assigned to
A or B with a seeded coin flip (seed 11); the bid graph drafts with that strategy, the harness approves as a human
would, ten simulated days pass, and reply rates are compared with a two-proportion z-test and Fisher's exact test.
The strategy is a config value (`writer.strategy`) and a per-thread override (`strategy` in the graph input), so the
winner ships with a one-line change.

**Results** (`evals/results/ab_test_evidence_first_vs_concise_question.json`, seed 11, deterministic writer):

| Strategy | Proposals | Replies | Reply rate | Hires |
|---|---|---|---|---|
| evidence_first | 143 | 94 | 65.7% | 21 |
| concise_question | 114 | 65 | 57.0% | 11 |

Two-proportion z = 1.43, p = 0.15; Fisher exact p = 0.16. **Not significant** at this sample size, so the default
stays `evidence_first`; earlier runs pointed the same way, which argues for more samples rather than a claim.

**Threats to validity:** the client model is ours and transparent (`gigsim/app.py::score_proposal`), so the test
measures which strategy fits *that* model; assignment is per job, so persona mix is balanced only in expectation;
the test is run once per seed — rerun with other seeds before trusting a small effect.
