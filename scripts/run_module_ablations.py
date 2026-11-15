"""Reuse the v10 runner, fixture, recorder and backend export for two ablations."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_main_ablations as runner
from scripts import run_strengthened_baselines as shared
from clarification_ablation_v1.modules import ModuleAblationMethod, VARIANTS, VERSION

original_sources = runner.source_hashes
original_config = runner.configuration
original_export = runner.export


def sources():
    result = original_sources()
    result['scripts/run_module_ablations.py'] = shared.sha(Path(__file__))
    return result


def configuration(args, cases):
    cfg = original_config(args, cases)
    # The legacy two-factor fields describe different mechanisms and must not
    # accidentally label the new module flags as dependency/gate ablations.
    cfg.pop('deterministic_dependency_propagation')
    cfg.pop('pending_obligation_terminal_gate')
    clarify, prune = VARIANTS[args.variant]
    cfg.update(clarification=clarify, selective_pruning=prune,
        actual_policy_preserved=True, final_sql_permission_gate='unchanged',
        no_pruning_semantics='whole_request_refusal_on_required_reduction',
        no_clarification_semantics='initial_residual_no_user_turns',
        backend_prompt='baseline_same_as_frozen_main',
        full_reference='frozen_historical_main_2800; explicit full run is optional')
    return cfg


def export(out, cases):
    original_export(out, cases)
    rows = shared.jsonl(out/'evaluation_rows.jsonl')
    for r in rows:
        trace = shared.read(Path(r['trace']))
        audit = trace.get('ablation') or {}
        r['has_pruning'] = bool(audit.get('delivered_with_reduction'))
        # No-pruning whole refusal denies the complete request, but does not
        # imply that a partial output has been delivered or pruned.
        denied = r['has_pruning'] or r['outcome'] == 'refuse'
        complete = r['outcome'] in {'deliver', 'refuse'}
        r['complete_request_decision'] = ('denied' if denied else 'allowed') if complete else 'unknown'
        r['classification'] = (('FN' if denied else 'TP') if r['original_permission']=='allowed'
            else ('TN' if denied else 'FP')) if complete else None
        r['module_ablation'] = audit
        r['clarification_quality'] = (trace.get('delivery') or {}).get('quality')
    for name, data in [('evaluation_rows.jsonl', rows),
                       ('clarified_requests.jsonl', shared.generation_inputs(rows))]:
        (out/name).write_text(''.join(__import__('json').dumps(r,ensure_ascii=False)+'\n' for r in data),encoding='utf8')
    stats=runner.summarize(rows,len(cases))
    stats.update(primary_metrics='fixed_capacity_SQL_completion_and_permission_violations',
                 decision_rates='auxiliary_frontend_only')
    runner.module('storage').atomic_json(out/'SUMMARY.json',stats)
    return stats


def main():
    runner.AblationMethod = ModuleAblationMethod
    runner.VARIANTS = VARIANTS
    runner.VERSION = VERSION
    runner.source_hashes = sources
    runner.configuration = configuration
    runner.export = export
    runner.main()


if __name__=='__main__':
    main()
