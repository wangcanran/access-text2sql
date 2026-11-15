"""Paired fresh responses at saved real deletion failure states."""
import json
from copy import deepcopy

from ...live import load_case
from ...recorder import Recorder, cost
from ...runtime import ROOT, snapshot_sources
from ...storage import atomic_json
from .hybrid import HybridUpdate
from .deletion_plan import deletion_region
from .lightweight import transaction
from .direct_removal import DirectRemovalUpdate
from .pacing import RateGate

SOURCE = ROOT / 'artifacts/clarification_main/20261021_hybrid_e2e_v1/hybrid'


def checkpoint(qid):
    r = json.loads((SOURCE / f'{qid}_1_reduction.json').read_text(encoding='utf8'))
    context = load_case(qid, 1)[1]
    if qid == 113:
        session = deepcopy(r['episode']['snapshots'][-1])
        for c in r['calls']:
            if c['stage'] != 'deletion_snapshot':
                continue
            p = json.loads(c['messages'][1]['content'])
            if p['event']['target'] not in ('f8', 'f9', 'f11'):
                continue
            event = {**p['event'], 'decision': p['event']['purpose']}
            if event['target'] == 'f11':
                return session, event, context
            raw = json.loads(c['response']['choices'][0]['message']['content'])
            # Reconstruct the archived state with its original scope defaults.
            # This is replay, not a fresh response to the current model contract.
            session = transaction(session, session['view'], event, raw, context,
                                  editable_ids=deletion_region(session['view'], event['target']))
        raise ValueError('Saved finance checkpoint missing')
    c = next(c for c in r['calls'] if c['stage'] == 'deletion_snapshot')
    p = json.loads(c['messages'][1]['content'])
    return deepcopy(r['episode']['snapshots'][0]), {**p['event'], 'decision': p['event']['purpose']}, context


def run(args):
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError('Checkpoint output must be new or empty')
    args.out.mkdir(parents=True, exist_ok=True)
    atomic_json(args.out / 'CONFIG.json', {'mode': 'saved_failure_state_fresh_paired_responses',
        'model': args.model, 'source': str(SOURCE), 'source_sha256': snapshot_sources(args.out / 'sources'),
        'gold_sql_to_agent': False})
    gate = RateGate(args.min_call_interval)
    for qid in (113, 1461):
        original, event, context = checkpoint(qid)
        for name, method in [('hybrid', HybridUpdate()), ('direct', DirectRemovalUpdate())]:
            session = deepcopy(original)
            record = {'question_id': qid, 'variant': name, 'model': args.model, 'calls': [],
                      'before': session['view'], 'event': event}
            path = args.out / f'{qid}_{name}.json'
            recorder = Recorder(args.model, 4, [], timeout=120, empty_retries=1, transport_retries=1)
            recorder.client.chat.completions.create = gate.wrap(recorder.client.chat.completions.create)
            call = recorder.bind(record, path, 0, name)
            try:
                result = method._transaction(call, session, session['view'], event, context)
                record.update(outcome='updated', after=result['view'],
                              audit=result['transaction_audit'][-1])
            except Exception as exc:
                record.update(outcome='error', error_type=type(exc).__name__)
            record['cost'] = cost(record['calls'])
            atomic_json(path, record)
            print(qid, name, record['outcome'], record['cost'], flush=True)
