# Balanced partial-service experiment

[Read the extended-experiments supplement](../../supplementary/extended_experiments.pdf).

This post-submission analysis evaluates how output fulfillment changes as requests with authorized partial-output references become more common. The primary cohort contains 486 fully authorized requests (A) and 486 strictly verified partial-reference requests (P), with 3,888 recorded outputs across Direct + RBAC, Serial, Policy, and ACCESS. All results use DeepSeek Flash.

The analysis reuses real outputs from the original study and the 445-request expansion. Constructing the balanced cohort does not involve new model inference. Cohort selection uses reference eligibility and input metadata rather than candidate performance.

## Contents

| File | Purpose |
|---|---|
| `extended_experiments.md` | English report text and tables generated with the PDF |
| `data/strict_metrics.csv` | Overall, partition, dataset, level, and origin results |
| `data/strict_composition.csv` | Exact cohort counts by partition, dataset, level, and origin |
| `data/strict_ratio_curve.csv` | Actual A/P counts and SafeEX/Full endpoints at every curve point |
| `data/strict_selection.json` | Selection seed, stratum quotas, cluster counts, and original selection hash |
| `data/strict_results.json` | Results, bootstrap comparisons, resampling summaries, and outcomes |
| `data/paired_cluster_bootstrap.csv` | Paired source-question comparisons for SafeEX and Full |
| `data/A_resampling_summary.csv` | Summary of all 1,000 alternative A-subset selections |
| `data/legacy_results.json` | Separate results using the original reference convention |
| `data/protocol.json` | Recorded retrospective analysis protocol |
| `data/expansion_provenance.json` | Construction counts, original source hashes, and expansion run parameters |
| `data/*audit_summary.json` | Reference and historical scoring audit summaries |
| `data/package_verification.json` | Verification result for the complete source experiment package |
| `data/SOURCE_TABLES.json` | Original table paths and byte hashes |
| `data/TABLE_HASHES.json` | Hashes of the frozen report inputs |
| `plot_curves.py`, `build_supplement.py` | Figure and PDF generation |

All metric values in the CSVs are percentages. Ranges in the results and shaded curve bands are scoring evidence bounds, not confidence intervals. Bootstrap intervals describe paired source-question resampling. Full measures fulfillment of the fixed reference, which can cover only part of a P request's original goals.

## Rebuild the PDF

From the repository root, in an environment with `matplotlib` and `reportlab`:

```bash
python extensions/ap_balance/plot_curves.py
python extensions/ap_balance/build_supplement.py
```

The second command verifies `data/TABLE_HASHES.json` before creating `supplementary/extended_experiments.pdf` and the English Markdown report. No model, API, or database is used to rebuild the document. Rendering-library versions can change the resulting PDF bytes while leaving the frozen data unchanged.

## Reproduction scope

This directory publishes the report inputs and build scripts. The audit summaries are records of the original package checks, not a new SQL re-execution by the report builder. Full request-level reaggregation requires the complete experiment package, including its per-request predictions, references, and selection scripts. Native SQL re-execution additionally requires the original database snapshots. Neither the full package nor database files are included in this report directory.

The strict cohort and the separate 525 A + 525 P cohort overlap and must not be pooled as independent experiments. The original `supplement.pdf` and the submitted-paper results remain unchanged.
