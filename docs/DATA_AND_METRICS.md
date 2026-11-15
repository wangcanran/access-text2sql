# Data and metrics

## Fixture format

| File | Contents |
|---|---|
| sources.json | Original requests, levels, semantic units and reference SQL |
| roles.json | Database/role/policy-source ID and column policy |
| schemas.json | Tables, columns, foreign keys and descriptions |
| selection.json | Cases, source/policy hashes, allowed columns and payload hashes |
| MANIFEST.json | Construction and provenance notes |

examples/synthetic_sales/ is created by tools/make_demo_fixture.py and excluded from paper metrics. The complete source request and reference SQL support the simulated user, not the clarification agent.

The paper has 2,800 requests including L0/L1/L2 and role assignments, not 2,800 per level. A request is an evaluated question/level/role-policy combination. Original labels partition the cohort into A (1,402 allowed) and 1,398 denied requests. P contains 80 denied requests with retained-output references; E = A union P has 1,482. The remaining 1,318 denied requests are outside E.

## Generation

The paper uses data_generation/flat_disclosure_experiment/. Retention levels are 0, 0.5 and 1, with L2 preserving the original request. The loader passes question_id, db_id, question and optional difficulty, excluding SQL, policy and RBAC labels. Model review and human verification are separate stages.

~~~bash
cd data_generation
python -m flat_disclosure_experiment.batch_generate --input /path/to/questions.json --schemas /path/to/schemas.json --output /path/to/generated --dry-run
python scripts/generate_flat_cross_dataset.py --help
~~~

Configure credentials and remove --dry-run to generate. Supply input/schema paths explicitly; historical defaults refer to the original experiment. Review/postprocessing code is in data_generation/scripts/. New generation is not the sealed paper cohort.

## Evidence

SafeEX credits jointly matching target columns only when candidate SQL is authorized and preserves complete rows, duplicates and required ordering. Targets are fixed independently of predictions. Full means complete fixed-reference fulfillment; a denied request's reference can cover only part of its original goals. Refusal earns zero SafeEX. SHR measures safe handling on originally denied requests; BSS combines output and handling scores.

The evidence includes paper_scores.jsonl.gz (29,640 rows), paper_handling.jsonl.gz (27,960), ablation_scores.jsonl.gz (4,446), ablation_handling.jsonl.gz (4,194), and expected.json. Native P reconciliation is included. SQL and raw model conversations are omitted; re-evaluating correctness requires external databases, references and candidates.

extensions/p_expansion/ contains 445 new post-submission requests' result summaries and historical orchestration code. Its deployment paths require configuration. It is separate from the original P subset.
