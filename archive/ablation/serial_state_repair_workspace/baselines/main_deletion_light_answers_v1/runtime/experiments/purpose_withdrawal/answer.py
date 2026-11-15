"""Apply the same replacement boundary used to authorize the selected menu."""
from copy import deepcopy

from ...engine import analysis
from ..decision_update.ledger import pending
from ..decision_update.lightweight import region, node_payload, protected_outputs, schema_payload
from ..decision_update.question_snapshot import ANSWER, commit_snapshot
from .removal import event_payload


def permission_answer(call, session, base, event, context):
    event = deepcopy(event)
    purpose = event['turn']['scoped_permission_decision']
    event['decision'] = purpose
    editable = region(base, event)
    edges = [e for e in base['relations']['edges']
             if {e['source_id'], e['target_id']} & editable]
    visible = editable | {e[k] for e in edges for k in ('source_id', 'target_id')}
    nodes = []
    for iid, node in base['items'].items():
        if iid not in visible:
            continue
        row = node_payload(node)
        row.pop('questions')
        row['previous_unresolved'] = [
            {'mode': 'menu' if d['dimension'] in ('scope', 'field') else 'detail',
             'question': d['question']} for d in pending(base) if d['owner_id'] == iid]
        nodes.append(row)
    payload = {'event': event_payload(event), 'editable_ids': sorted(editable),
        'preserve_outputs': sorted(protected_outputs(base, event) & editable),
        'nodes': nodes, 'edges': [[e[k] for k in ('source_id', 'target_id', 'kind')] for e in edges],
        'schema': schema_payload(context)}
    replaceable = set(purpose.get('replaceable_columns', []))
    selected = {c for o in event['turn'].get('selected_options', []) for c in o['required_columns']}
    retired = (replaceable - selected) & set(base['items'][event['target']]['required_columns'])

    def commit(raw):
        if not isinstance(raw, dict):
            raise ValueError('Answer response must be an object')
        raw = deepcopy(raw)
        # Code owns retirement for the actual selected replacement, not every
        # dependency of the containing purpose. No second semantic classifier.
        declared = raw.get('retire_columns', {})
        if not isinstance(declared, dict):
            raise ValueError('retire_columns must be an object')
        if any(not isinstance(cs, list) or any(not isinstance(c, str) for c in cs)
               or not set(cs) <= (retired if parent == event['target'] else set())
               for parent, cs in declared.items()):
            raise ValueError('Answer cannot retire dependencies outside the selected replacement')
        raw['retire_columns'] = {event['target']: sorted(retired)}
        return commit_snapshot(session, base, event, raw, context)

    prompt = ANSWER + '''\nThe permission menu already fixed the replacement boundary.
event.purpose.replaceable_columns are the only replaceable old dependencies.
Keep the unchanged subject/association and independent requirements. Express the
selected replacement, not the old replaced field. Code retires replaced columns
not selected again; do not retire any other dependency.
'''
    return analysis.validated_call(call, 'answer_question_snapshot', prompt, payload, commit)
