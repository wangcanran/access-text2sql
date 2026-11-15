"""Serial fresh live runner, exact frozen base plus separately hashed overlay."""
import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from .baseline import ROOT, FROZEN, module
from .method import DeferredDeletion, VERSION


def overlay_hashes():
    folder = Path(__file__).resolve().parent
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.glob('*.py'))}


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--cases', default='24:0,113:1,796:0')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--model', default='gpt-5.5')
    parser.add_argument('--turns', type=int, default=30)
    parser.add_argument('--max-calls', type=int, default=120)
    args = parser.parse_args()
    cases = [tuple(map(int, c.split(':'))) for c in args.cases.split(',')]
    if len(set(cases)) != len(cases) or any(len(c) != 2 or c[1] not in (0, 1, 2) for c in cases):
        parser.error('Use distinct qid:level cases')
    if args.out.exists() and any(args.out.iterdir()):
        parser.error('Output directory must be new or empty')
    args.out.mkdir(parents=True, exist_ok=True)
    module('environment').load_dotenv(ROOT / 'clarification_main/.env')
    runtime = module('runtime')
    source = runtime.snapshot_sources(args.out / 'sources')
    overlay = overlay_hashes()
    for name in overlay:
        target = args.out / 'overlay_sources' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((Path(__file__).parent / name).read_bytes())
    atomic_json = module('storage').atomic_json
    atomic_json(args.out / 'CONFIG.json', {
        'method': VERSION, 'baseline': str(FROZEN), 'model': args.model,
        'cases': cases, 'turns': args.turns, 'max_calls': args.max_calls,
        'cache': False, 'replay': False, 'gold_sql_simulator_only': True,
        'runtime_source_sha256': source, 'overlay_source_sha256': overlay,
    })
    live = module('live')
    recorder_class = live.Recorder
    gate = module('experiments.decision_update.pacing').RateGate(2.0)
    def recorder_factory(*a, **kw):
        r = recorder_class(*a, **kw)
        r.client.chat.completions.create = gate.wrap(r.client.chat.completions.create)
        return r
    for qid, level in cases:
        run_args = argparse.Namespace(out=args.out, model=args.model, turns=args.turns,
            max_calls=args.max_calls, reuse_dir=[], response_retries=1, transport_retries=1,
            clarity_first=True, disable_deterministic_empty_relations=False)
        with patch.object(live, 'MainMethod', lambda **kw: DeferredDeletion()), patch.object(live, 'Recorder', recorder_factory):
            outcome = live.run_case(run_args, qid, level)
        path = args.out / f'{qid}_{level}_reduction.json'
        record = json.loads(path.read_text(encoding='utf8'))
        record['config'].update(method=VERSION, integrated_method=VERSION,
            deletion='block_questions_keep_canonical_graph_commit_after_clarification',
            normal_answers='frozen_light_answer_transactions')
        record['overlay_unchanged'] = overlay_hashes() == overlay
        record['overlay_source_sha256'] = overlay
        atomic_json(path, record)
        print('FINISHED', qid, level, outcome, record['actual_cost'], flush=True)


if __name__ == '__main__':
    main()
