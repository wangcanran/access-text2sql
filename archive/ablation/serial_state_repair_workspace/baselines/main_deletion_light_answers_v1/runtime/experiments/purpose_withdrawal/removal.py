"""Withdraw the same local contribution that was compiled for the menu.

The compiler owns semantic decomposition. Code fixes a whole-owner removal;
the update model handles only independent survivors and affected neighbours.
No secondary judge, string-based purpose matching or permission-count heuristic.
"""
from copy import deepcopy

from ...engine import analysis
from ..decision_update.deletion_plan import deletion_region, snapshot_response
from ..decision_update.lightweight import (event_payload as base_event_payload, node_payload, protected_outputs,
                          schema_payload, transaction)


REMOVE = '''Apply the withdrawal event to the active local requirements.
Return JSON only: {"updates":{"f1":[{"id":"p1","role":"output",
"kind":"attribute","text":"surviving requirement","tables":["t"],
"columns":["t.c"],"questions":{"field":"Which information?"}}]},
"edges":[["p1","f2","applies_to"]]}.
Only editable_ids may be updated. Omitted parents stay unchanged. [] removes a
parent. Each survivor needs id, text, tables, columns; questions is the COMPLETE
remaining scope/field/operation/value list ({} when clear). Splits need roles.

event.purpose is the SAME local contribution used for the menu, not a new task
to reinterpret. Its required_columns describe operands, not independent outputs.
fixed_removals are already removed by code. Do not update or recreate them.
For a compound target, event.purpose.remaining_requirements lists its independent
surviving meanings. Preserve those from the source, not the withdrawn purpose.
Do not replace a withdrawn requirement by an unspecified subject, field, summary
or new question about that same requirement. Remove its exclusive parameters,
time windows and associations too. Retain independently needed shared operands.
Read-only nodes belong to other owners; never copy them into the target.

Review affected neighbours: preserve each preserve_outputs contribution, and
retain shared modifiers for their surviving targets. Remove an exclusive modifier
if its only subject was removed. A surviving subject may be ambiguous, but do not
invent a new subject or silently erase an independent association. Recompute
tables and columns from surviving meaning, including join operands. Existing
modifiers remain modifiers; filter operands are not extra outputs. Preserve
AND/OR, negation, comparisons, quantities and unanswered independent choices.
Edges touching revised/split nodes: applies_to source qualifies target; context
source depends on target. Code keeps unchanged one-to-one attachments. No SQL,
permission explanations, external workarounds or historical intent restoration.
'''


def event_payload(event):
    payload = base_event_payload(event)
    decision = event.get('decision', {})
    payload['purpose'].update({k: decision[k] for k in
        ('required_columns', 'retained_columns', 'replaceable_columns') if k in decision})
    return payload


def whole_owner(base, event):
    """An explicit local decomposition, not an inferred empty/missing value."""
    purpose = event.get('decision', {})
    return (bool(purpose.get('requirement'))
            and purpose.get('role') == base['items'][event['target']]['role']
            and purpose.get('remaining_requirements') == [])


def remove_transaction(call, session, base, event, context):
    target = event['target']
    editable = deletion_region(base, target)
    fixed = {target} if whole_owner(base, event) else set()
    model_editable = editable - fixed
    edges = [e for e in base['relations']['edges']
             if {e['source_id'], e['target_id']} & editable]
    visible = editable | {e[k] for e in edges for k in ('source_id', 'target_id')}
    payload = {'event': event_payload(event), 'editable_ids': sorted(model_editable),
        'fixed_removals': sorted(fixed),
        'preserve_outputs': sorted((protected_outputs(base, event) & editable) - fixed),
        'nodes': [node_payload(n) for iid, n in base['items'].items() if iid in visible],
        'read_only_requirements': [
            {'id': iid, 'text': n['requirement'], 'role': n['role']}
            for iid, n in base['items'].items() if iid not in editable],
        'edges': [[e[k] for k in ('source_id', 'target_id', 'kind')] for e in edges],
        'schema': schema_payload(context)}

    def commit(raw):
        raw = deepcopy(raw)
        if not isinstance(raw, dict):
            raise ValueError('Withdrawal response must be an object')
        updates = raw.get('updates')
        if not isinstance(updates, dict) or not set(updates) <= model_editable:
            raise ValueError('Only editable survivors may be updated; fixed_removals are code-owned')
        updates.update({iid: [] for iid in fixed})
        if event.get('decision', {}).get('remaining_requirements') and updates.get(target) == []:
            raise ValueError('Independent remainders require surviving children of the target')
        raw = snapshot_response(raw, base)
        for rows in updates.values():
            if not isinstance(rows, list):
                raise ValueError('Survivors must be an array')
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get('questions'), dict):
                    raise ValueError('Each survivor needs its complete questions object, {} when clear')
        # An ID replacement is not a deletion. Detect an exact unchanged local
        # snapshot without requiring text quotes or a falling denied-column count.
        if not fixed and target in updates and len(updates[target]) == 1:
            n, row = base['items'][target], updates[target][0]
            if (row.get('text', '').strip() == n['requirement'].strip()
                    and set(row['columns']) == set(n['required_columns'])
                    and set(row['tables']) == set(n['scope_tables'])
                    and row.get('questions', {}) == n['question_focus']
                    and row.get('role', n['role']) == n['role']
                    and row.get('kind', n['kind']) == n['kind']):
                raise ValueError('Withdrawal must remove its local purpose, not rename an unchanged node')
        result = transaction(session, base, event, raw, context,
                             editable_ids=editable, keep_shared=True)
        result['needs_final_render'] = True
        result['transaction_audit'][-1]['withdrawal'] = {
            'policy': 'menu_bound_purpose_v1', 'fixed_removals': sorted(fixed)}
        return result

    # Nothing semantic remains to revise; no model call or blanket cascade.
    if fixed and not model_editable:
        return commit({'updates': {}})
    return analysis.validated_call(call, 'deletion_snapshot', REMOVE, payload, commit)
