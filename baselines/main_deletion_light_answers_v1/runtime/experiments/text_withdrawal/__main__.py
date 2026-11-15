"""Serial live test of isolated withdrawal, with private SQL only for the simulator."""
import argparse
import json
from pathlib import Path
from unittest.mock import patch

from ... import live
from ...runtime import snapshot_sources, runtime_hashes
from ...storage import atomic_json
from ..decision_update.pacing import RateGate
from .method import TextWithdrawal, VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', default='113:0')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--model', default='gpt-5.5')
    parser.add_argument('--turns', type=int, default=30)
    parser.add_argument('--max-calls', type=int, default=60)
    parser.add_argument('--min-call-interval', type=float, default=2.)
    args = parser.parse_args()
    try:
        cases = [tuple(map(int, s.split(':'))) for s in args.cases.split(',')]
        if any(len(c) != 2 or c[1] not in (0, 1, 2) for c in cases):
            raise ValueError
    except ValueError:
        parser.error('--cases needs comma-separated question_id:level pairs')
    if len(set(cases)) != len(cases):
        parser.error('Duplicate cases')
    if args.max_calls < 1 or args.turns < 0 or args.min_call_interval < 0:
        parser.error('Invalid call/turn/interval budget')
    if args.out.exists() and any(args.out.iterdir()):
        parser.error('--out must be new or empty')
    args.out.mkdir(parents=True, exist_ok=True)
    hashes = snapshot_sources(args.out / 'sources')
    atomic_json(args.out / 'CONFIG.json', {**vars(args), 'out': str(args.out.resolve()),
        'runtime_source_sha256': hashes, 'variant': VERSION,
        'gold_sql_simulator_only': True, 'baseline': 'purpose_withdrawal:menu_bound_purpose_v1',
        'serial': True, 'fresh_calls': True})
    original_recorder = live.Recorder
    gate = RateGate(args.min_call_interval)

    def recorder_factory(*a, **kw):
        recorder = original_recorder(*a, **kw)
        recorder.client.chat.completions.create = gate.wrap(recorder.client.chat.completions.create)
        return recorder

    run_args = argparse.Namespace(out=args.out, model=args.model, turns=args.turns,
        max_calls=args.max_calls, reuse_dir=[], response_retries=1, transport_retries=1,
        clarity_first=True, disable_deterministic_empty_relations=False)
    for qid, level in cases:
        with patch.object(live, 'MainMethod', lambda **kw: TextWithdrawal()), \
                patch.object(live, 'Recorder', recorder_factory):
            outcome = live.run_case(run_args, qid, level)
        path = args.out / f'{qid}_{level}_reduction.json'
        record = json.loads(path.read_text(encoding='utf8'))
        record['config'].update(reconstruction='text_rewrite_on_withdrawal_final_graph_render',
            structure_update='local_answers_fresh_graph_on_withdrawal', deletion=VERSION,
            prompt_profile='snapshot_with_text_withdrawal')
        record['experiment'] = {'variant': VERSION, 'serial': True, 'fresh_calls': True,
            'baseline': 'purpose_withdrawal:menu_bound_purpose_v1', 'single_removal_entry': True,
            'relation_joint_operands': True, 'question_annotation': 'menu_or_detail',
            'old_question_merge': False, 'gold_sql_simulator_only': True}
        record['runtime_unchanged'] = runtime_hashes() == hashes
        atomic_json(path, record)
        print(VERSION, qid, level, outcome, record['cost'], flush=True)


if __name__ == '__main__':
    main()
