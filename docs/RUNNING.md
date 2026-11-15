# Running experiments

## Environment and frontends

Use Python 3.11. requirements.txt records experimental server package versions; requirements-optional.txt adds plotting and PostgreSQL support. .env.example documents variables but is not automatically loaded by every runner.

PowerShell:
~~~powershell
$env:DEEPSEEK_API_KEY = "YOUR_KEY"
python tools/run_frontend.py --method access --fixture examples/synthetic_sales --out runs/access_demo --model deepseek-flash
~~~

Bash:
~~~bash
export DEEPSEEK_API_KEY="YOUR_KEY"
python tools/run_frontend.py --method access --fixture examples/synthetic_sales --out runs/access_demo --model deepseek-flash
~~~

The model identifier records the experiment setting. Choose an available model/endpoint for new runs; API outputs may differ. These runs call a paid external API.

| --method | Meaning |
|---|---|
| access | Frozen main method |
| serial | BIRD-inspired clarification, then RBAC SQL backend |
| policy | Same conversational backbone with agent policy injection |
| no_clarification | Main ablation without user exchanges |
| no_pruning | Main ablation without selective pruning |
| clarify_then_prune | Serial lifecycle ablation |

Use --check-only for offline validation, --cases 0:2 for one question/level pair, and --resume only with the same configuration. Multi-role fixtures require unique case identities according to the original runners.

The frontend produces handoff/trace artifacts, not executed SQL answers. The locked main runner can return a nonzero status when acceptance checks detect unresolved outcomes; inspect the summary and traces before classifying an infrastructure failure.

## SQL generation

~~~bash
python scripts/predict_rbac_cloud_only.py --help
~~~

Set RBAC_API_BASE and RBAC_API_KEY for an OpenAI-compatible endpoint. Use its RBAC input format, not selection.json. Historical conversion is in scripts/prepare_clarification_text2sql_inputs.py and scripts/predict_rbac_gold_qwen.py.

Direct uses the structured RBAC prompt; Serial uses the RBAC backend; Policy and ACCESS use the Text-to-SQL-only backend. Preserve request rows with --no_dedupe. Paper generation uses temperature 0 and a 4,096-token output limit.

## Results and recovery protocols

~~~bash
python tools/reproduce_tables.py --out runs/tables
~~~

Outputs are main_table.csv, ablation_table.json and verification.json. Evidence endpoints retain scoring uncertainty, not bootstrap sampling uncertainty.

archive/evaluation/ contains original scoring scripts requiring databases, references, candidate SQL and the historical layout. Configure placeholder paths before use. The portable offline entrypoint reaggregates tables; it does not execute SQL.

Final ablation results selected terminal outcomes under archived recovery protocols. A fresh clean ablation run does not reproduce that selection. Recovery source and protocols are in archive/ablation/. The full-method reference reuses the main experiment.
