"""Serial, fresh-input comparison; private gold SQL is supplied only to simulator."""
import argparse
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

from ... import live
from ...runtime import snapshot_sources, runtime_hashes
from ...storage import atomic_json
from .method import DecisionUpdate
from .lightweight import LightweightUpdate
from .deletion_plan import DeletionPlanUpdate, VERSION as DELETION_VERSION
from .preplanned import PreplannedUpdate
from .hybrid import HybridUpdate, VERSION as HYBRID_VERSION
from .direct_removal import DirectRemovalUpdate
from .budget import BudgetUpdate
from .question_snapshot import QuestionSnapshotUpdate
from .pacing import RateGate


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases', default='796:0,24:0', help='Comma-separated question_id:level')
    p.add_argument('--variants', default='clarity,fused',
                   help='baseline,clarity,fused,light,deletion_plan,preplanned,hybrid,direct,schema,memo,budget,snapshot; serial execution')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--model', default='gpt-5.5')
    p.add_argument('--turns', type=int, default=30)
    p.add_argument('--max-calls', type=int, default=120)
    p.add_argument('--min-call-interval', type=float, default=2.0)
    p.add_argument('--removal-checkpoints', action='store_true', help='Paired responses at saved deletion failure states')
    args = p.parse_args()
    if args.removal_checkpoints:
        from .removal_checkpoints import run
        return run(args)
    cases = [tuple(map(int, s.split(':'))) for s in args.cases.split(',')]
    variants = args.variants.split(',')
    if any(v not in ('baseline', 'clarity', 'fused', 'light', 'deletion_plan', 'preplanned', 'hybrid', 'direct', 'schema', 'memo', 'budget', 'snapshot') for v in variants):
        p.error('Unknown variant')
    if len(set(cases)) != len(cases) or len(set(variants)) != len(variants):
        p.error('Duplicate cases/variants')
    if args.out.exists() and any(args.out.iterdir()):
        p.error('Output directory must be new or empty')
    args.out.mkdir(parents=True, exist_ok=True)
    hashes = snapshot_sources(args.out / 'sources')
    atomic_json(args.out / 'CONFIG.json', {**vars(args), 'out': str(args.out.resolve()),
                'runtime_source_sha256': hashes, 'gold_sql_simulator_only': True})
    original = live.MainMethod
    original_recorder = live.Recorder
    gate = RateGate(args.min_call_interval)
    def recorder_factory(*a, **kw):
        recorder = original_recorder(*a, **kw)
        recorder.client.chat.completions.create = gate.wrap(recorder.client.chat.completions.create)
        return recorder
    for variant in variants:
        folder = args.out / variant
        folder.mkdir()
        atomic_json(folder / 'CONFIG.json', {'runtime_source_sha256': hashes, 'variant': variant})
        for qid, level in cases:
            timings, marker = [], time.perf_counter()
            instances = []

            def factory(**options):
                method = (QuestionSnapshotUpdate() if variant == 'snapshot' else
                        BudgetUpdate(pack=variant != 'memo', memo=variant != 'schema')
                        if variant in ('schema', 'memo', 'budget') else
                        DirectRemovalUpdate() if variant == 'direct' else
                        HybridUpdate() if variant == 'hybrid' else
                        PreplannedUpdate() if variant == 'preplanned' else
                        DeletionPlanUpdate() if variant == 'deletion_plan' else
                        LightweightUpdate() if variant == 'light' else
                        DecisionUpdate() if variant == 'fused' else original(**options))
                instances.append(method)
                return method

            original_run = live.engine_module('harness').run_session

            def timed_run(call, request, context, db_name, allowed, ask_user, **kwargs):
                nonlocal marker
                def ask(question, history):
                    nonlocal marker
                    ready = time.perf_counter()
                    timings.append({'kind': 'question_ready', 'agent_wait_seconds': ready - marker})
                    answer = ask_user(question, history)
                    marker = time.perf_counter()
                    timings[-1]['simulator_seconds'] = marker - ready
                    return answer
                try:
                    return original_run(call, request, context, db_name, allowed, ask, **kwargs)
                finally:
                    timings.append({'kind': 'terminal_or_error', 'agent_wait_seconds': time.perf_counter() - marker})

            run_args = argparse.Namespace(out=folder, model=args.model, turns=args.turns,
                max_calls=args.max_calls, reuse_dir=[], response_retries=1, transport_retries=1,
                clarity_first=variant != 'baseline', disable_deterministic_empty_relations=False)
            with (patch.object(live, 'MainMethod', factory), patch.object(live, 'Recorder', recorder_factory),
                  patch.object(live.engine_module('harness'), 'run_session', timed_run)):
                outcome = live.run_case(run_args, qid, level)
            path = folder / f'{qid}_{level}_reduction.json'
            record = json.loads(path.read_text(encoding='utf8'))
            record['experiment'] = {'variant': variant, 'fused_refinement_mapping': variant == 'fused',
                'decision_conservation': variant == 'fused', 'permission_lineage_merged': False,
                'fresh_calls': True, 'latency': timings}
            if variant == 'light':
                record['config'].update(reconstruction='active_graph_final_only',
                    structure_update='atomic_semantic_transaction', deletion='classified_code_or_semantic_transaction')
                record['experiment'].update(decision_conservation=True, permission_lineage_merged=True,
                    shared_decision_annotation=False, offline_range_guard='partitive_some_of')
            if variant == 'deletion_plan':
                record['config'].update(reconstruction='active_graph_final_only',
                    structure_update='atomic_semantic_transaction',
                    deletion='subtractive_snapshot_then_code_for_classified_units')
                record['experiment'].update(decision_conservation=True,
                    permission_lineage_merged=True, shared_decision_annotation=False,
                    deletion_plan=DELETION_VERSION)
            if variant == 'preplanned':
                record['config'].update(reconstruction='active_graph_final_only',
                    structure_update='atomic_semantic_transaction',
                    deletion='conditional_withdrawal_compiled_with_menu')
                record['experiment'].update(decision_conservation=True,
                    permission_lineage_merged=True, shared_decision_annotation=False,
                    deletion_plan='menu_preplanned_withdrawal_v1')
            if variant == 'hybrid':
                record['config'].update(reconstruction='active_graph_final_only',
                    structure_update='atomic_semantic_transaction',
                    deletion='whole_purpose_code_else_local_snapshot',
                    prompt_profile='lightweight_with_hybrid_role_explicit_render')
                record['experiment'].update(decision_conservation=True,
                    permission_lineage_merged=True, shared_decision_annotation=False,
                    deletion_plan=HYBRID_VERSION,
                    dependency_ownership_certified=False)
            if variant == 'direct':
                record['config'].update(reconstruction='active_graph_final_only',
                    structure_update='atomic_semantic_transaction',
                    deletion='bound_purpose_to_local_survivors',
                    prompt_profile='lightweight_with_hybrid_role_explicit_render')
                record['experiment'].update(decision_conservation=True,
                    permission_lineage_merged=True, shared_decision_annotation=False,
                    deletion_plan='direct_local_removal_v1',
                    compiled_remainders=False, dependency_ownership_certified=False)
            if variant in ('schema', 'memo', 'budget'):
                record['config'].update(reconstruction='active_graph_final_only',
                    structure_update='atomic_semantic_transaction',
                    deletion='whole_purpose_code_else_local_snapshot',
                    prompt_profile='staged_schema_exact_memo_v1')
                record['budget_audit'] = [a for method in instances for a in method.budget_audit]
                record['experiment'].update(schema_packing=variant != 'memo',
                    compiled_decision_memo=variant != 'schema',
                    decision_conservation=True, permission_lineage_merged=True,
                    shared_decision_annotation=False, dependency_ownership_certified=False)
            if variant == 'snapshot':
                record['config'].update(reconstruction='active_graph_final_only',
                    structure_update='atomic_local_question_snapshot',
                    deletion='whole_purpose_code_else_local_snapshot')
                record['experiment'].update(question_annotation='menu_or_detail',
                    old_question_merge=False, shared_decision_annotation=False,
                    offline_range_guard=False, permission_lineage_merged=True)
            record['runtime_unchanged'] = runtime_hashes() == hashes
            atomic_json(path, record)
            print(variant, qid, level, outcome, record['cost'], flush=True)


if __name__ == '__main__':
    main()
