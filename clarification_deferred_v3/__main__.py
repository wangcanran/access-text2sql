"""Fresh serial regression live with fixed expanded-v2 policy fixtures."""
import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from clarification_deferred_deletion.baseline import ROOT, module
from clarification_deferred_deletion.__main__ import overlay_hashes
from .method import TypedDeferredDeletion, VERSION


def hashes(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.glob('*.py'))}


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--cases', default='1343:0,883:1,760:0,42:0,24:0')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--model', default='gpt-5.5')
    p.add_argument('--check-only', action='store_true')
    args = p.parse_args()
    cases = [tuple(map(int, c.split(':'))) for c in args.cases.split(',')]
    if len(set(cases)) != len(cases) or any(len(c) != 2 or c[1] not in (0, 1, 2) for c in cases):
        p.error('Distinct qid:level cases required')
    live = module('live')
    selection = json.loads((ROOT / 'artifacts/clarification_main/deferred_v2_expanded_six_live_20261025/SELECTION.json').read_text(encoding='utf8'))
    policies = json.loads(live.POLICIES.read_text(encoding='utf8'))
    schemas = json.loads(live.SCHEMAS.read_text(encoding='utf8'))
    loaded = {}
    for q, level in cases:
        f = next((f for f in selection['cases'] if (f['question_id'], f['level']) == (q, level)), None)
        if f is None:
            loaded[q, level] = live.load_case(q, level)
            continue
        source = live.load_source(live.DATA, q)
        assert source['source_sha256'] == f['source_sha256']
        role = next(r for r in policies if r['db_id'] == f['db_id'] and r['role'] == f['role']
                    and r['metadata']['question_id'] == f['policy_source_question_id'])
        assert hashlib.sha256(json.dumps(role['policy'], sort_keys=True).encode()).hexdigest() == f['policy_sha256']
        context = live.schema_context(schemas[source['db_id']])
        allowed = live.engine_module('policy').allowed_columns(context, role['policy'])
        assert set(allowed) == set(f['allowed_columns'])
        loaded[q, level] = source, context, role, allowed, live.load_reference_sql(live.GOLD, source)
    previous = json.loads((ROOT / 'artifacts/clarification_main/deferred_deletion_v2_three_live_20261024/CONFIG.json').read_text(encoding='utf8'))
    assert overlay_hashes() == previous['overlay_source_sha256'], 'v2 sources changed'
    if args.check_only:
        print('Validated', len(cases), 'fixed fixtures and immutable v2 provenance')
        return
    if args.out.exists() and any(args.out.iterdir()):
        p.error('Output directory must be new or empty')
    args.out.mkdir(parents=True, exist_ok=True)
    module('environment').load_dotenv(ROOT / 'clarification_main/.env')
    runtime = module('runtime')
    source_hashes = runtime.snapshot_sources(args.out / 'sources')
    v2_hashes = overlay_hashes()
    v3_folder = Path(__file__).resolve().parent
    v3_hashes = hashes(v3_folder)
    for label, folder, expected in [('v2', ROOT / 'clarification_deferred_deletion', v2_hashes),
                                     ('v3', v3_folder, v3_hashes)]:
        for name in expected:
            target = args.out / (label + '_sources') / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((folder / name).read_bytes())
    atomic = module('storage').atomic_json
    atomic(args.out / 'CONFIG.json', dict(method=VERSION, cases=cases, model=args.model,
        runtime_source_sha256=source_hashes, overlay_v2_sha256=v2_hashes, overlay_v3_sha256=v3_hashes,
        cache=False, replay=False, turns=30, max_calls=120,
        policy_fixture_source='deferred_v2_expanded_six_live_20261025/SELECTION.json',
        changes='typed_question_contract_and_identity_preserving_continuations',
        unchanged='decomposition,permission_closure,deferred_commit_review'))
    recorder_class = live.Recorder
    gate = module('experiments.decision_update.pacing').RateGate(2.0)
    def recorder(*a, **kw):
        r = recorder_class(*a, **kw)
        r.client.chat.completions.create = gate.wrap(r.client.chat.completions.create)
        return r
    for q, level in cases:
        run_args = argparse.Namespace(out=args.out, model=args.model, turns=30, max_calls=120,
            reuse_dir=[], response_retries=1, transport_retries=1, clarity_first=True,
            disable_deterministic_empty_relations=False)
        with patch.object(live, 'load_case', lambda qid, lev: loaded[qid, lev]), \
             patch.object(live, 'MainMethod', lambda **kw: TypedDeferredDeletion()), \
             patch.object(live, 'Recorder', recorder):
            outcome = live.run_case(run_args, q, level)
        path = args.out / f'{q}_{level}_reduction.json'
        d = json.loads(path.read_text(encoding='utf8'))
        d['config'].update(method=VERSION, integrated_method=VERSION,
                           deletion='unchanged_v2_deferred_commit', answers='typed_obligation_transactions')
        d['overlay_unchanged'] = overlay_hashes() == v2_hashes and hashes(v3_folder) == v3_hashes
        atomic(path, d)
        print('FINISHED', q, level, outcome, d['actual_cost'], flush=True)


if __name__ == '__main__':
    main()
