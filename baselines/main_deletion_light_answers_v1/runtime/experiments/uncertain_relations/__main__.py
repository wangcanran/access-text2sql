"""Run the uncertain-relation clarification experiment independently."""
import argparse
import json
from pathlib import Path
from unittest.mock import patch

from ... import live
from ...runtime import snapshot_sources, runtime_hashes
from ...storage import atomic_json
from ..decision_update.pacing import RateGate
from .method import UncertainRelationClarification, VERSION

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases', default='113:1')
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--model', default='gpt-5.5')
    p.add_argument('--turns', type=int, default=20)
    p.add_argument('--max-calls', type=int, default=60)
    p.add_argument('--min-call-interval', type=float, default=2.)
    args = p.parse_args()
    cases = [tuple(map(int, x.split(':'))) for x in args.cases.split(',')]
    if args.out.exists() and any(args.out.iterdir()):
        p.error('--out must be new or empty')
    args.out.mkdir(parents=True, exist_ok=True)
    hashes = snapshot_sources(args.out / 'sources')
    atomic_json(args.out / 'CONFIG.json', {**vars(args), 'out': str(args.out.resolve()), 'version': VERSION, 'runtime_source_sha256': hashes, 'gold_sql_simulator_only': True, 'fresh_calls': True})
    original = live.Recorder
    gate = RateGate(args.min_call_interval)
    def recorder_factory(*a, **kw):
        recorder = original(*a, **kw)
        recorder.client.chat.completions.create = gate.wrap(recorder.client.chat.completions.create)
        return recorder
    rows = []
    for qid, level in cases:
        out = args.out / f'{qid}_{level}'
        out.mkdir()
        atomic_json(out / 'CONFIG.json', {**vars(args), 'out': str(out.resolve()),
            'version': VERSION, 'runtime_source_sha256': hashes,
            'gold_sql_simulator_only': True, 'fresh_calls': True})
        run_args = argparse.Namespace(out=out, model=args.model, turns=args.turns, max_calls=args.max_calls, reuse_dir=[], response_retries=1, transport_retries=1, clarity_first=True, disable_deterministic_empty_relations=False)
        with patch.object(live, 'MainMethod', lambda **kw: UncertainRelationClarification()), patch.object(live, 'Recorder', recorder_factory):
            outcome = live.run_case(run_args, qid, level)
        path = out / f'{qid}_{level}_reduction.json'
        record = json.loads(path.read_text(encoding='utf8'))
        record['experiment'] = {'version': VERSION, 'variant': 'uncertain_relations', 'gold_sql_simulator_only': True}
        record['runtime_unchanged'] = runtime_hashes() == hashes
        atomic_json(path, record)
        row = {'case': [qid, level], 'outcome': outcome, 'cost': record['cost'], 'relation_audit': record.get('episode', {}).get('session', {}).get('relation_clarification_audit', [])}
        rows.append(row)
        atomic_json(args.out / 'SUMMARY.json', rows)
        print(json.dumps(row, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
