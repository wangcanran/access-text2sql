"""Fixed-state experiment: reuse annotated purposes instead of relocating them.

This is a diagnostic, not an end-to-end variant. Annotation is measured as a
separate call here; integrating it into analysis would require another test.
"""
import argparse
from copy import deepcopy
import json

from ...engine.analysis import validated_call, indexes
from ...engine.execution import _authorized_graph, _connected, validate_sql_dependencies
from ...engine.simple_session import connected_options
from ...live import load_case
from ...recorder import Recorder, cost
from ...runtime import ROOT, snapshot_sources
from ...storage import atomic_json
from .ledger import pending
from .lightweight import LightweightUpdate, node_payload, schema_payload
from .pacing import RateGate


ANNOTATE = '''Analyze the current owner's independent business purposes once.
Return only a JSON object with a purposes array. Each purpose has:
id: unique string; decision_ids: array of provided IDs;
requirement: the actual meaning of this contribution in the current request;
role: output (a final returned value) or modifier (a condition/qualification);
kind: attribute, calculation, relation or instruction;
scope_status: fixed or unresolved; scope_tables: array of table IDs;
required_columns: array of column IDs; question: a business menu question;
field_candidates: array of objects with column and label strings.
Preserve all independent purposes in owner, including already clear filters.
Use provided decision_ids for unresolved menu decisions belonging to each
purpose; clear contributions have []. Do not fold unrelated decisions together.
required_columns are dependencies already implied by this contribution,
including determined operands and necessary join keys. Candidate fields are
alternatives, not dependencies until chosen. Never infer an unspecified field,
coverage, value, or formula. A filter is not an additional returned output.
scope_tables identifies the actual business subject, not every storage table.
Give every purpose a nonempty question about which business operand/information
to use, including a currently clear purpose that may later require confirmation.
Questions ask which business operand/information to use for this purpose,
without revealing access diagnostics or naming a workaround. Alternatives must
address that same purpose; unrelated available fields are not substitutes.
No SQL. Use literal schema IDs, no source quotation matching is required.
'''


def check_purposes(raw, source, decisions, context):
    columns = indexes(context)[1]
    tables = {t['name'] for t in context['tables']}
    available = {d['decision_id'] for d in decisions if d['dimension'] in ('scope', 'field')}
    rows = deepcopy(raw['purposes'])
    if not isinstance(rows, list) or not rows:
        raise ValueError('Expected nonempty purposes')
    seen, assigned = set(), set()
    for row in rows:
        if not isinstance(row.get('id'), str) or row['id'] in seen:
            raise ValueError('Purpose IDs must be unique')
        seen.add(row['id'])
        ids = row['decision_ids']
        if not isinstance(ids, list) or len(ids) != len(set(ids)) or not set(ids) <= available or assigned & set(ids):
            raise ValueError('Assign each existing menu decision at most once')
        assigned.update(ids)
        if row.get('role') not in ('output', 'modifier') or row.get('kind') not in ('attribute', 'calculation', 'relation', 'instruction'):
            raise ValueError('Invalid purpose role/kind')
        if row.get('scope_status') not in ('fixed', 'unresolved'):
            raise ValueError('Invalid scope status')
        for key in ('requirement', 'question'):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError('Empty purpose/question')
        for key, universe in [('scope_tables', tables), ('required_columns', columns)]:
            if not isinstance(row.get(key), list) or any(c not in universe for c in row[key]):
                raise ValueError('Invalid schema references')
        if not row['scope_tables']:
            raise ValueError('Purpose needs a subject scope')
        if not isinstance(row.get('field_candidates'), list) or any(
                c.get('column') not in columns or not isinstance(c.get('label'), str) or not c['label'].strip()
                for c in row['field_candidates']):
            raise ValueError('Invalid candidates')
    if assigned != available:
        raise ValueError('Missing menu decision IDs: ' + repr(sorted(available - assigned)))
    return rows


def reuse_decision(purposes, *, decision_ids=(), pending_columns=()):
    """Route by recorded identity/dependencies, never by wording similarity."""
    denied, ids = set(pending_columns), set(decision_ids)
    matches = [p for p in purposes if (set(p['required_columns']) & denied if denied
                                      else set(p['decision_ids']) & ids)]
    if len(matches) != 1:
        # A shared dependency needs an explicit multi-purpose decision; guessing
        # one owner here would recreate the original failure.
        return None
    chosen = matches[0]
    return {**deepcopy(chosen), 'quote': chosen['requirement'],
            'origin_dimension': 'permission' if denied else 'field',
            'remaining_requirements': [p['requirement'] for p in purposes if p['id'] != chosen['id']]}


def authorized_leaves(decision, session, source, context):
    if decision is None:
        return None
    target = {**source, **{k: decision[k] for k in ('scope_tables', 'requirement', 'role', 'kind')}}
    return [c for c in decision['field_candidates'] if c['column'] in session['allowed']
            and connected_options(session, context, target, [c['column']])]


def run(args):
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError('Use a new output directory')
    args.out.mkdir(parents=True, exist_ok=True)
    atomic_json(args.out / 'CONFIG.json', {'model': args.model, 'mode': 'fixed_state_purpose_reuse',
        'end_to_end': False, 'gold_sql_to_agent': False,
        'source_hashes': snapshot_sources(args.out / 'sources')})
    gate = RateGate(2)
    specs = [(1461, 1, r) for r in range(1, args.student_repeats + 1)] + [(113, 0, 1), (796, 0, 1)]
    for qid, level, repeat in specs:
        directory = ('20261022_deletion_scope_fix_live_v3' if qid != 796
                     else '20261022_question_snapshot_matrix_v1')
        path = ROOT / f'artifacts/clarification_main/{directory}/snapshot/{qid}_{level}_reduction.json'
        saved = json.loads(path.read_text(encoding='utf8'))
        session = deepcopy(saved['episode']['snapshots'][0])
        selected = next((q for q in session['view']['questions'] if q['dimension'] == 'permission'), None)
        if selected is None:
            selected = next(q for q in session['view']['questions'] if q['dimension'] == 'field')
        source = session['view']['items'][selected['item']['item_id']]
        decisions = [d for d in pending(session['view']) if d['owner_id'] == source['item_id']
                     and d['dimension'] in ('scope', 'field')]
        denied = source['denied_columns'] if selected['dimension'] == 'permission' else []
        record = {'question_id': qid, 'level': level, 'repeat': repeat, 'source_trace': str(path),
                  'model': args.model, 'calls': [], 'selected_question': selected}
        output = args.out / f'{qid}_{level}_{repeat}.json'
        recorder = Recorder(args.model, 8, [], timeout=120, empty_retries=1, transport_retries=1)
        recorder.client.chat.completions.create = gate.wrap(recorder.client.chat.completions.create)
        call = recorder.bind(record, output, 0, 'probe')
        try:
            purposes = validated_call(call, 'purpose_annotation', ANNOTATE,
                {'owner': node_payload(source), 'open_decisions': decisions, 'schema': schema_payload(load_case(qid, level)[1])},
                lambda raw: check_purposes(raw, source, decisions, load_case(qid, level)[1]))
            _, context, _, allowed, reference = load_case(qid, level)
            direct = reuse_decision(purposes, pending_columns=denied,
                                    decision_ids=selected.get('decision_ids', []))
            method = LightweightUpdate()
            method._active_question_focus = selected['focus']
            legacy = method.compile_decision(call, {'source': source, 'pending_columns': denied}, context,
                                             field=not denied)
            record.update(outcome='compared', annotated_purposes=purposes,
                reused=direct, relocated=legacy,
                reused_authorized_leaves=authorized_leaves(direct, session, source, context),
                relocated_authorized_leaves=authorized_leaves(legacy, session, source, context))
            if qid == 113:
                graph, error = _authorized_graph(context, allowed)
                record['permission_audit'] = {'graph_error': error,
                    'authorized_edges': {t: sorted(v) for t, v in graph.items()},
                    'connectivity': {','.join(pair): _connected(graph, pair) for pair in
                                     [('client', 'trans'), ('client', 'card'), ('account', 'trans'), ('account', 'card')]},
                    # Private reference used only after calls, never in payload.
                    'reference_sql_check': validate_sql_dependencies(reference['sql'], context, allowed)}
        except Exception as exc:
            record.update(outcome='error', error_type=type(exc).__name__, error=str(exc))
        record['cost'] = cost(record['calls'])
        atomic_json(output, record)
        print(qid, level, repeat, record['outcome'], record['cost'], flush=True)


if __name__ == '__main__':
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--model', default='gpt-5.5')
    parser.add_argument('--student-repeats', type=int, default=2)
    run(parser.parse_args())
