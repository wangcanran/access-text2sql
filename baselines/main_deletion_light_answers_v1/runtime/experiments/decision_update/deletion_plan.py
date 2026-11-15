"""Opt-in subtraction: classify once, delete units in code, revise compounds once."""
from copy import deepcopy

from ...engine import analysis, session_update
from .lightweight import (LightweightUpdate, region, transaction, node_payload,
                          event_payload, protected_outputs, shared_review_ids,
                          schema_payload, deletion_patch)


VERSION = 'subtractive_snapshot_v4'

REMOVE = '''Withdraw only event.purpose from the active requirements. Return JSON:
{"updates":{"f1":[{"id":"p1","role":"output","text":"surviving requirement",
"tables":["t"],"columns":["t.c"],"questions":{"field":"Which information?"}}]},
"edges":[["p1","f2","applies_to"]]}.
Omit unchanged parents; [] removes a whole parent. Changed children require id,
text, tables, columns; kind may inherit its parent. Recompute tables for EACH
survivor: only its current simultaneous entity anchors, excluding withdrawn
requirements and alternative candidate storage. Never inherit a removed scope.
If its subject remains uncertain, preserve that uncertainty with a scope question.
When splitting a parent, state
role for EVERY child: output for returned values, modifier for conditions and
context. An applies_to source is a modifier, never an additional returned value.
For a one-child update, role may inherit its parent. questions is the COMPLETE
set of still-unresolved scope/field/operation/value questions for that child;
omit questions when none remain. Do not output pending IDs or retire_columns.
Code owns IDs, old-state retention and dependency retirement.

Only editable_ids may change. Preserve every preserve_outputs contribution.
Derive survivors from the editable nodes themselves. read_only_requirements are
already retained by code: they provide context, not requirements to recreate
inside another parent. Each survivor must belong to its declared original parent.
Withdraw dependent parameters with their purpose, never independent requirements.
Split compound survivors into independently withdrawable outputs and modifiers.
One shared modifier must have the SAME meaning for every applies_to target;
different output-specific calculations belong in separate modifiers.
Keep subjects/associations explicit. Removing an identifier filter does not remove
an independent association. If a surviving subject is uncertain, retain a scope
question; never silently substitute a different subject or invent a binding.
Outputs are returned values; modifiers stay modifiers. A profile heading is not
another output. Never turn a surviving predicate into an output.

Use only active meaning, literal schema columns and existing ambiguity. No new
requirements, SQL, permissions or workarounds. Candidates are optional.
Edges: applies_to means source qualifies target; context means source depends on
target. Return edges touching changed/split nodes; unchanged and same-role
one-to-one edges are retained by code. Omitted parents stay unchanged, including
shared modifiers and their surviving attachments. Review shared wording/questions
and update the parent only if withdrawal changes their meaning.
'''


def deletion_region(view, target):
    """Review semantic dependants and direct qualifications, not all siblings."""
    ids = set(session_update.affected_region(view, target))
    ids |= shared_review_ids(view, {target})
    return ids


def snapshot_response(raw, base):
    """Normalize snapshot-only metadata and roles implied by typed edges."""
    raw = deepcopy(raw)
    qualifiers = {e[0] for e in raw.get('edges', [])
                  if isinstance(e, list) and len(e) == 3 and e[2] == 'applies_to'}
    for parent, rows in raw.get('updates', {}).items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if not isinstance(row.get('tables'), list):
                raise ValueError('Deletion survivor requires explicit current tables for parent ' + parent)
            row.pop('pending', None)  # Not part of a survivor snapshot.
            if row.get('id') in qualifiers and 'role' not in row:
                row['role'] = 'modifier'
            elif len(rows) > 1 and 'role' not in row:
                raise ValueError('Split children require explicit roles')
    return raw


def build_plan(view):
    """Create a conservative, code-owned deletion capability description.

    The decision compiler must additionally certify whole-unit withdrawal.
    Relation/instruction nodes may establish subjects and need semantic review.
    """
    units = set(view.get('progressive', {}).get('deletion_plan_units', []))
    edges = view.get('relations', {}).get('edges', [])
    incoming = {}
    outgoing = {}
    for edge in edges:
        incoming.setdefault(edge['target_id'], []).append(edge)
        outgoing.setdefault(edge['source_id'], []).append(edge)
    entries = {}
    for iid, node in view.get('items', {}).items():
        if iid not in units:
            mode = 'semantic'
            reason = 'not_decomposed_as_an_independent_unit'
        elif node['role'] == 'output':
            mode = 'output'
            reason = 'classified_output_contribution'
        elif node['role'] == 'modifier' and node.get('kind') not in ('instruction', 'relation'):
            mode = 'qualification'
            reason = 'classified_non_anchor_modifier'
        else:
            mode = 'anchor'
            reason = 'possible_subject_or_association_provider'
        entries[iid] = {
            'mode': mode,
            'reason': reason,
            'incoming': len(incoming.get(iid, [])),
            'outgoing': len(outgoing.get(iid, [])),
        }
    return {'version': VERSION, 'inventory_id': view.get('inventory_id'),
            'entries': entries}


def _code_raw(view, target):
    """Return a transaction that removes target and orphaned modifiers.

    The surviving edges are copied mechanically. The private ``keep_shared``
    flag tells the validator that an unchanged shared modifier does not need a
    semantic review merely because one target was removed.
    """
    dropped = {target}
    edges = view.get('relations', {}).get('edges', [])
    while True:
        before = len(dropped)
        for iid, node in view.get('items', {}).items():
            if iid in dropped or node['role'] != 'modifier':
                continue
            targets = {e['target_id'] for e in edges
                       if e['source_id'] == iid and e['kind'] == 'applies_to'}
            if targets and targets <= dropped:
                dropped.add(iid)
        if len(dropped) == before:
            break
    editable = set(region(view, {'kind': 'removal', 'target': target}))
    retained_edges = [[e['source_id'], e['target_id'], e['kind']]
                      for e in edges if e['source_id'] not in dropped
                      and e['target_id'] not in dropped
                      and ({e['source_id'], e['target_id']} & editable)]
    return {'updates': {iid: [] for iid in sorted(dropped)},
            'edges': retained_edges, 'retire_columns': {}}


def code_deletable(view, target, decision=None):
    """Whether deleting target can be expressed without semantic rewriting."""
    plan = view.get('deletion_plan') or {}
    entry = plan.get('entries', {}).get(target)
    if (not entry or plan.get('version') != VERSION
            or plan.get('inventory_id') != view.get('inventory_id')):
        return False, 'no current deletion plan'
    if entry['mode'] == 'semantic':
        return False, entry['reason']
    # A surviving node that uses the target as a linguistic antecedent must be
    # reviewed; removing a node that itself refers to another node is safe.
    if any(e['target_id'] == target and e['kind'] == 'context'
           for e in view.get('relations', {}).get('edges', [])):
        return False, 'a surviving context-dependent node may lose its antecedent'
    if (not isinstance(decision, dict)
            or decision.get('remaining_requirements') != []
            or decision.get('role') != view['items'][target]['role']):
        return False, 'the current decision does not certify a whole-unit withdrawal'
    if entry['mode'] == 'anchor':
        # Removing the anchor while outputs survive can leave phrases such as
        # “the associated account” without a valid source.  Let one semantic
        # transaction decide whether to restate or retire those outputs.
        survivors = [i for i, n in view['items'].items()
                     if i != target and n['role'] == 'output']
        if survivors:
            return False, 'subject or association anchor has surviving outputs'
    return True, entry['reason']


class DeletionPlanUpdate(LightweightUpdate):
    """Opt-in comparison arm for low-frequency deletion updates."""

    def commit_transaction(self, session, base, event, raw, context):
        return transaction(session, base, event, raw, context, compound_answers=True)

    def _attach_plan(self, session):
        if session.get('view'):
            session['view']['deletion_plan'] = build_plan(session['view'])
            session['view'].setdefault('update_audit', {})['deletion_plan'] = VERSION
        return session

    def _transaction(self, call, session, base, event, context):
        if event['kind'] != 'removal':
            result = super()._transaction(call, session, base, event, context)
        else:
            editable = deletion_region(base, event['target'])
            edges = [e for e in base['relations']['edges']
                     if {e['source_id'], e['target_id']} & editable]
            visible = editable | {e[k] for e in edges for k in ('source_id', 'target_id')}
            removal_event = event_payload(event)
            # The compiler's remainder is only a routing hint. It cannot
            # authorize importing another node's contribution into this parent.
            removal_event['purpose'].pop('remaining_requirements', None)
            payload = {'event': removal_event, 'editable_ids': sorted(editable),
                'preserve_outputs': sorted(protected_outputs(base, event) & editable),
                'nodes': [node_payload(n) for iid, n in base['items'].items() if iid in visible],
                'read_only_requirements': [
                    {'id': iid, 'text': n['requirement'], 'role': n['role']}
                    for iid, n in base['items'].items() if iid not in editable],
                'edges': [[e[k] for k in ('source_id', 'target_id', 'kind')] for e in edges],
                'schema': schema_payload(context)}

            def commit(raw):
                raw = snapshot_response(raw, base)
                # Snapshot omission means unchanged for every owner. Retain
                # surviving shared attachments mechanically; do not demand the
                # model recopy every edge just to preserve an unchanged parent.
                return transaction(session, base, event, raw, context,
                                   editable_ids=editable, keep_shared=True)

            result = analysis.validated_call(call, 'deletion_snapshot', REMOVE, payload, commit)
        info = result.get('view', {}).get('progressive', {})
        units = set(info.get('deletion_plan_units', []))
        replacements = (result.get('transaction_audit') or [{}])[-1].get('replacements', {})
        for parent, children in replacements.items():
            if parent in set(base.get('progressive', {}).get('deletion_plan_units', [])) and len(children) == 1:
                units.add(children[0])
            if event.get('kind') == 'removal' and parent == event.get('target') and len(children) > 1:
                # A model-proposed split enables code deletion only after the
                # next compiler also certifies that the whole unit is withdrawn.
                units.update(children)
        if info:
            info['deletion_plan_units'] = sorted(units & set(result['view']['items']))
        if event.get('kind') == 'removal':
            result['needs_final_render'] = True
        return self._attach_plan(result)

    def _code_remove(self, session, basis, q, trigger, context, reason):
        base = basis['view']
        raw = _code_raw(base, q['item']['item_id'])
        event = {'kind': 'removal', 'target': q['item']['item_id'],
                 'dimension': q['dimension'], 'question': q['focus'],
                 'decision': q.get('scoped_permission_decision', {})}
        new = transaction(session, base, event, raw, context, keep_shared=True)
        new['needs_final_render'] = True
        # The requirement graph, rather than this projected text, remains the
        # active source after edits. A final render reconnects the surviving
        # outputs and active modifiers once, after all menu decisions finish.
        new = self._attach_plan(new)
        audit = new.setdefault('reduction_audit', [])
        audit.append({'after_turn': len(session['turns']), 'trigger': trigger,
            'target_fragment': q['item']['span'], 'target_role': q['item']['role'],
            'question': q['focus'], 'asked_dimension': q['dimension'],
            'previous_request': session['current_request'],
            'request': new['current_request'], 'source_inventory': base['inventory_id'],
            'method': VERSION, 'analysis_mode': 'code_deletion_plan',
            'code_reason': reason,
            'receipt': {'request': new['current_request'],
                        'drop_ids': [p for p, cs in raw['updates'].items() if not cs],
                        'revise_ids': []}})
        new['transaction_audit'][-1]['execution'] = VERSION
        return new

    def _remove(self, call, session, basis, q, trigger, context):
        base = basis.get('view')
        if base is not None:
            decision = q.get('scoped_permission_decision', {})
            allowed, reason = code_deletable(base, q['item']['item_id'], decision)
            if allowed:
                return self._code_remove(session, basis, q, trigger, context, reason)
        target = q['item']['item_id']
        event = {'kind': 'removal', 'target': target, 'dimension': q['dimension'],
                 'question': q['focus'], 'decision': q.get('scoped_permission_decision', {})}
        # Keep the old safe fast path for unsplit independent nodes, but never
        # bypass the anchor checks above through its less restrictive policy.
        anchor = (base['items'][target]['role'] == 'modifier' and
                  base['items'][target]['kind'] in ('relation', 'instruction'))
        provider = any(e['kind'] == 'context' and e['target_id'] == target
                       for e in base['relations']['edges'])
        fast = None if anchor or provider else deletion_patch(base, target, event['decision'])
        if fast is not None:
            return self._code_remove(session, basis, q, trigger, context, 'independent_whole_node')
        new = self._transaction(call, session, base, event, context)
        new['transaction_audit'][-1]['execution'] = 'deletion_snapshot'
        replacements = new['transaction_audit'][-1]['replacements']
        new.setdefault('reduction_audit', []).append({
            'after_turn': len(session['turns']), 'trigger': trigger,
            'target_fragment': q['item']['span'], 'target_role': q['item']['role'],
            'question': q['focus'], 'asked_dimension': q['dimension'],
            'previous_request': session['current_request'], 'request': new['current_request'],
            'source_inventory': base['inventory_id'], 'method': VERSION,
            'scoped_permission_decision': event['decision'], 'analysis_mode': 'deletion_snapshot',
            'receipt': {'request': new['current_request'],
                'drop_ids': [p for p, cs in replacements.items() if not cs],
                'revise_ids': [p for p, cs in replacements.items() if cs]}})
        return new

    def analyze_session(self, call, session, context, reconstruct=None):
        result = super().analyze_session(call, session, context, reconstruct)
        if result.get('view') and not result.get('needs_final_render'):
            result['view']['deletion_plan'] = build_plan(result['view'])
        return result

