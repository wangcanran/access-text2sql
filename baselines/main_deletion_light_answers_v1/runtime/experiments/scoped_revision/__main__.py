"""Serial comparison of scoped revision and the preceding prepared updater."""
import argparse
import json
from pathlib import Path
from unittest.mock import patch

from ... import live
from ...runtime import snapshot_sources, runtime_hashes
from ...storage import atomic_json
from ..decision_update.pacing import RateGate
from ..prepared_update.method import PreparedUpdate
from .method import ScopedRevision, VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', default='796:0,113:1')
    parser.add_argument('--variants', default='revision')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--model', default='gpt-5.5')
    parser.add_argument('--turns', type=int, default=20)
    parser.add_argument('--max-calls', type=int, default=40)
    parser.add_argument('--min-call-interval', type=float, default=2.)
    args = parser.parse_args()
    factories = {'revision': ScopedRevision, 'prepared': PreparedUpdate}
    variants = args.variants.split(',')
    try:
        cases = [tuple(map(int, c.split(':'))) for c in args.cases.split(',')]
        if (any(len(c) != 2 or c[1] not in (0, 1, 2) for c in cases)
                or len(set(cases)) != len(cases) or len(set(variants)) != len(variants)
                or any(v not in factories for v in variants)
                or args.turns < 0 or args.max_calls < 1 or args.min_call_interval < 0):
            raise ValueError
    except ValueError:
        parser.error('Invalid cases, variants or budgets')
    if args.out.exists() and any(args.out.iterdir()):
        parser.error('--out must be new or empty')
    args.out.mkdir(parents=True, exist_ok=True)
    hashes = snapshot_sources(args.out / 'sources')
    atomic_json(args.out / 'CONFIG.json', {
        **vars(args), 'out': str(args.out.resolve()), 'version': VERSION,
        'runtime_source_sha256': hashes, 'serial': True,
        'gold_sql_simulator_only': True, 'fresh_calls': True,
    })
    original = live.Recorder
    gate = RateGate(args.min_call_interval)

    def recorder_factory(*a, **kw):
        recorder = original(*a, **kw)
        recorder.client.chat.completions.create = gate.wrap(recorder.client.chat.completions.create)
        return recorder

    summary = []
    for variant in variants:
        out = args.out / variant
        out.mkdir()
        atomic_json(out / 'CONFIG.json', {
            'runtime_source_sha256': hashes, 'variant': variant, 'version': VERSION})
        run_args = argparse.Namespace(out=out, model=args.model, turns=args.turns,
            max_calls=args.max_calls, reuse_dir=[], response_retries=1,
            transport_retries=1, clarity_first=True,
            disable_deterministic_empty_relations=False)
        for qid, level in cases:
            with patch.object(live, 'MainMethod', lambda **kw: factories[variant]()), \
                    patch.object(live, 'Recorder', recorder_factory):
                outcome = live.run_case(run_args, qid, level)
            path = out / f'{qid}_{level}_reduction.json'
            record = json.loads(path.read_text(encoding='utf8'))
            record['experiment'] = {'variant': variant, 'version': VERSION,
                'gold_sql_simulator_only': True, 'serial': True}
            record['config'].update(method=variant,
                reconstruction='active_graph_final_only',
                structure_update='located_semantic_revision' if variant == 'revision' else 'prepared_update',
                prompt_profile='light_located_revision' if variant == 'revision' else 'light_prepared_update',
                deletion='cancel_current_purpose')
            record['runtime_unchanged'] = runtime_hashes() == hashes
            atomic_json(path, record)
            row = {'variant': variant, 'case': [qid, level], 'outcome': outcome,
                   'cost': record['cost']}
            summary.append(row)
            atomic_json(args.out / 'SUMMARY.json', summary)
            print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
