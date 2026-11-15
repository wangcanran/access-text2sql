"""Frozen-source contract-v5 live runner; cases use existing fixed policy fixtures."""
import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from clarification_deferred_deletion.baseline import ROOT, module
from clarification_deferred_deletion.__main__ import overlay_hashes
from clarification_deferred_v3_2.__main__ import hashes
from .method import ContractMethod, VERSION


def source_hashes(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob('*.py')}


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--cases', default=None)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--selection', type=Path,
                   default=ROOT/'artifacts/clarification_main/deferred_v4_1_heldout20_live_20261025/SELECTION.json')
    p.add_argument('--check-only', action='store_true')
    args = p.parse_args()
    selection_data = json.loads(args.selection.read_text(encoding='utf8'))
    pairs = ([tuple(map(int, value.split(':'))) for value in args.cases.split(',')]
             if args.cases else [(r['question_id'], r['level']) for r in selection_data['cases']])
    if len(pairs) != len(set(pairs)):
        p.error('Duplicate cases')
    live = module('live')
    selected = selection_data['cases']
    roles = json.loads(live.POLICIES.read_text(encoding='utf8'))
    schemas = json.loads(live.SCHEMAS.read_text(encoding='utf8'))
    loaded = {}
    for qid, level in pairs:
        row = next((r for r in selected if (r['question_id'], r['level']) == (qid, level)), None)
        if not row:
            raise ValueError('No fixed selection for '+str((qid, level)))
        source = live.load_source(live.DATA, qid)
        assert source['source_sha256'] == row['source_sha256']
        role = next(r for r in roles if r['db_id'] == row['db_id'] and r['role'] == row['role']
                    and r['metadata']['question_id'] == row['policy_source_question_id'])
        assert hashlib.sha256(json.dumps(role['policy'], sort_keys=True).encode()).hexdigest() == row['policy_sha256']
        context = live.schema_context(schemas[row['db_id']])
        allowed = live.engine_module('policy').allowed_columns(context, role['policy'])
        assert set(allowed) == set(row['allowed_columns'])
        loaded[qid, level] = source, context, role, allowed, live.load_reference_sql(live.GOLD, source)
    if args.check_only:
        print('Validated', len(pairs), 'fixed fixtures')
        return
    existing = list(args.out.iterdir()) if args.out.exists() else []
    if any(x.name != 'SELECTION.json' for x in existing):
        p.error('Output must be new or empty')
    args.out.mkdir(parents=True, exist_ok=True)
    runtime_hashes = module('runtime').snapshot_sources(args.out/'sources')
    folders = [('v2', ROOT/'clarification_deferred_deletion'),
               ('v4_1', ROOT/'clarification_deferred_v3_2'),
               ('v5', Path(__file__).resolve().parent)]
    for label, folder in folders:
        for source in folder.glob('*.py'):
            target = args.out/(label+'_sources')/source.name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(source.read_bytes())
    config = dict(method=VERSION, cases=pairs, model='gpt-5.5',
                  runtime_source_sha256=runtime_hashes,
                  overlay_v2_sha256=overlay_hashes(),
                  overlay_v4_sha256=hashes(ROOT/'clarification_deferred_v3_2'),
                  overlay_v5_sha256=source_hashes(Path(__file__).resolve().parent),
                  cache=False, replay=False, first_attempts=True,
                  policy_fixture='deferred_v4_1_heldout20_live_20261025/SELECTION.json')
    atomic = module('storage').atomic_json
    atomic(args.out/'CONFIG.json', config)
    module('environment').load_dotenv(ROOT/'clarification_main/.env')
    for qid, level in pairs:
        run_args = argparse.Namespace(out=args.out, model='gpt-5.5', turns=30, max_calls=80,
            reuse_dir=[], response_retries=1, transport_retries=1, clarity_first=True,
            disable_deterministic_empty_relations=False)
        with patch.object(live, 'load_case', lambda q, l: loaded[q, l]), \
             patch.object(live, 'MainMethod', lambda **kw: ContractMethod()):
            outcome = live.run_case(run_args, qid, level)
        path = args.out/f'{qid}_{level}_reduction.json'
        d = json.loads(path.read_text(encoding='utf8'))
        d['config'].update(method=VERSION, integrated_method=VERSION)
        d['overlay_unchanged'] = (overlay_hashes() == config['overlay_v2_sha256'] and
            hashes(ROOT/'clarification_deferred_v3_2') == config['overlay_v4_sha256'] and
            source_hashes(Path(__file__).resolve().parent) == config['overlay_v5_sha256'])
        atomic(path, d)
        print('FINISHED', qid, level, outcome, d['actual_cost'], flush=True)


if __name__ == '__main__':
    main()
