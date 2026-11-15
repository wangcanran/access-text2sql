"""Serial comparison runner; Gold SQL is visible only to the simulator."""
import argparse
import json
from pathlib import Path
from unittest.mock import patch

from ... import live
from ...runtime import snapshot_sources, runtime_hashes
from ...storage import atomic_json
from ..decision_update.pacing import RateGate
from .method import TextState, VERSION


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
        'gold_sql_simulator_only': True, 'serial': True, 'fresh_calls': True})
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
        with patch.object(live, 'MainMethod', lambda **kw: TextState()), \
                patch.object(live, 'Recorder', recorder_factory):
            outcome = live.run_case(run_args, qid, level)
        path = args.out / f'{qid}_{level}_reduction.json'
        record = json.loads(path.read_text(encoding='utf8'))
        record['config'].update(method=VERSION, integrated_method=VERSION,
            reconstruction='current_request_plus_one_interaction',
            structure_update='fresh_graph_after_semantic_edit', deletion='unified_text_edit',
            progressive_fragments='fresh_snapshot', source_choices='menu_or_detail',
            prompt_profile=VERSION, final_render='direct_current_request')
        record['episode']['method'] = {'name': VERSION, 'analysis_mode': 'flat_compact',
            'update_mode': 'full', 'local_bundle': False, 'strict_menus': True,
            'category_navigation': 'code_only'}
        record['experiment'] = {'variant': VERSION, 'serial': True, 'fresh_calls': True,
            'single_semantic_writer': True, 'old_graph_input': False,
            'old_question_merge': False, 'gold_sql_simulator_only': True}
        record['runtime_unchanged'] = runtime_hashes() == hashes
        atomic_json(path, record)
        print(VERSION, qid, level, outcome, record['cost'], flush=True)


if __name__ == '__main__':
    main()
