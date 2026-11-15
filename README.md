# ACCESS

Code and supporting materials for permission-constrained intent refinement in Text-to-SQL.

ACCESS maintains a persistent intent state, checks local updates after clarification, and reviews dependency closures before committing removals.

[中文说明](README_zh.md) · [Running](docs/RUNNING.md) · [Data and metrics](docs/DATA_AND_METRICS.md) · [Provenance](docs/PROVENANCE.md)

## Quick start

Use Python 3.11 in a separate environment. Run from the repository root:

~~~bash
python -m pip install -r requirements.txt
python tools/reproduce_tables.py --out runs/tables
python tools/run_frontend.py --method access --fixture examples/synthetic_sales --out runs/demo --check-only
~~~

The table command recomputes 20 main method/model combinations and three ablations from bundled per-request evidence, without an API or database. It verifies aggregation, not SQL regeneration or execution. The frontend command validates a synthetic fixture and source locks without calling a model.

## Repository map

| Location | Contents |
|---|---|
| clarification_useful_residual_v6/ | Main state, clarification, checked updates, deferred removal |
| clarification_deferred_deletion/, clarification_deferred_v3_2/, clarification_contract_v5/ | Dependency and contract layers |
| baselines/main_deletion_light_answers_v1/ | Hash-locked underlying runtime |
| scripts/ | Experiment runners, strong baselines, SQL adapters |
| clarification_ablation_v1/ | Ablation implementations |
| data_generation/ | L0/L1/L2 construction and review |
| tools/ | Portable frontend, table reproduction and validation |
| supplementary/ | Metric evidence, environment and provenance |
| archive/ | Historical scoring and recovery scripts |
| extensions/p_expansion/ | Post-submission experiment |

Database files, model weights, credentials and raw API logs are external assets. Historical package names remain for source-lock compatibility. See [third-party notices](THIRD_PARTY_NOTICES.md) before publishing.

## Supplementary material

[Read the supplementary material](supplement.pdf).
