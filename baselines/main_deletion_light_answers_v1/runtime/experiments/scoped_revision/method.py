"""Locate a semantic unit before asking, then revise that unit non-destructively.

An adaptation of revision semantics, NOT a reproduction of SMCalFlow or a SQL AST.
The existing atomic transaction owns IDs, permission checks and unchanged nodes.
"""
from copy import deepcopy
from contextlib import contextmanager
from unittest.mock import patch

from ..menu_partition.method import MenuPartition
from ..cancel_answer.method import cancelling
from ..decision_update.lightweight import node_payload, answered_dimensions, transaction
from ..decision_update.ledger import pending
from ..decision_update.question_delta import asked_ids, apply_questions

VERSION = 'scoped_revision_v5_partition_and_boundary_edges'
REPLACEMENT_BOUNDARY = '''
For a permission_decision, also return replaceable_columns: the exact subset of
source.required_columns that a user's choice from field_candidates may replace.
This is not the list of denied columns. A dependency needed to connect the
stated population to the requested subject is common to every valid option and
must not be replaceable. For a relation, treat both endpoints and any anchor
that identifies the requested entity as jointly necessary. Use [] when the
menu only chooses attributes/operands within a fixed relation. For other
decision types return replaceable_columns: [].
Include this key in the JSON response for permission_decision only.
'''
LOCATE = '''
LOCATED REVISION: the question identifies a semantic argument of ONE requirement.
Inspect query_context to understand subjects and shared constraints. It is NOT
an instruction to add those requirements to source. Partition source BEFORE
asking whenever cancelling the asked purpose would leave an independent return
value, predicate or shared anchor inside it. Materialize those remainders now;
do not defer their discovery to the answer updater. Other existing nodes are
references, never children of source. Retain their IDs in attachment edges.
The named argument (field/scope/operation/value) and its parent requirement are
different: choosing fields does not choose coverage, a time window or a formula.
Route existing pending IDs to children that actually own their uncertainty.
Preserve required subject and join dependencies in their owning children.
No extra update_intent, dependency_coverage or write-set annotation is required.
The runner creates the revision locator from the actual compiled question.
Each existing pending ID must have exactly ONE owning child. When an argument
is shared (such as which subset of attributes), materialize ONE modifier node
owning that pending ID and attach it to all relevant outputs with applies_to.
Do not copy the ID/question into each output. Shared references are edges.
Use pending IDs for existing arguments, questions ONLY for genuinely new argument
dimensions. Changing wording is not a new argument. Do not keep the retired source
ID as an edge endpoint: edges use new child aliases or unchanged neighbor IDs.
'''
REVISE = '''
EXPLICIT REVISION: revision_locator identifies the selected requirement and
argument. Revise that location using the ACTUAL answer; read_only_query provides
references, not write authority. Do not resolve unrelated arguments merely
because they share a node or dependencies. Code preserves nodes outside
editable_ids, and preserves pending decisions unless explicitly changed.
Use existing decision IDs for a reworded/partially answered question; never
create a paraphrased replacement ID. Related decisions may close only when the
answer supplies their missing information. The current argument is not evidence
about another argument, such as coverage, subject or calculation method.
On cancellation, remove the located purpose and its exclusive qualifications,
not its independent siblings or a shared subject. If source was not adequately
partitioned, return its independent survivors instead of deleting the container.
Surviving references must still have an explicit subject and scope. If a cancelled
anchor makes another requirement ambiguous, do not invent an anchor: this is a
new uncertainty, not permission to silently drop that other requirement.
When cancelling a shared modifier, revise every directly attached output in
this same transaction. Keep each output and its independent meaning; remove its
reference to the cancelled anchor or make the remaining ambiguity explicit as
one shared unresolved requirement. Do not leave a surviving output dependent
on a retired modifier.

For a permission menu, revision_locator also contains selected_columns,
retained_columns, and replaced_columns established by the runner. Treat this as
an explicit overwrite boundary: describe the accepted alternative and retained
dependencies, and do not keep a replaced source dependency or the requirement
that depended on it. This boundary is code-validated; do not infer it from text.
'''


def locator(view, event):
    """Identity comes from the runner's event, never another model annotation."""
    target = event['target']
    decision = event.get('turn', {}).get('scoped_permission_decision', {})
    result = {
        'root': target,
        'argument': decision.get('origin_dimension', event['dimension']),
        'decision_ids': sorted(asked_ids(view, event, answered_dimensions(event))),
        'requirement_type': view['items'][target]['role'],
        'purpose': decision.get('requirement', view['items'][target]['requirement']),
        'operation': 'cancel' if cancelling(event) else 'revise',
    }
    turn = event.get('turn', {})
    if (event['kind'] == 'answer'
            and decision.get('origin_dimension') == 'permission'
            and turn.get('selected_options')):
        selected = {c for option in turn['selected_options']
                    for c in option.get('required_columns', [])}
        replaceable = set(decision.get('replaceable_columns', []))
        result.update(selected_columns=sorted(selected),
                      retained_columns=sorted(decision.get('retained_columns', [])),
                      replaced_columns=sorted(replaceable - selected))
    return result


def permission_menu_dependencies(source, decision, dimension):
    """Only explicit replacement metadata can remove common menu obligations."""
    required = set(source['required_columns']) | set(decision['required_columns'])
    if dimension != 'permission':
        return sorted(required - set(decision['required_columns']))
    replaceable = decision.get('replaceable_columns')
    if replaceable is None:
        return sorted(required)  # Missing metadata fails closed.
    return sorted(required - set(replaceable))


def argument_questions(base, event, raw, mapping, replacements, aliases, dimensions):
    """Existing arguments are addressed by identity, not regenerated prose.

    In an affected parent, a question hint in an already represented dimension
    cannot mint another argument. Distinct existing arguments of that same type
    remain distinct by pending IDs; new dimensions are still discovered normally.
    This intentionally does not guess semantic equivalence from wording.
    """
    old = pending(base)
    for parent, children in replacements.items():
        owned = {d['dimension'] for d in old if d['owner_id'] == parent}
        for row in mapping:
            if row['item_id'] in children:
                row['question_focus'] = {k: v for k, v in row['question_focus'].items()
                                         if k not in owned}
                row['missing'] = list(row['question_focus'])
    return apply_questions(base, event, raw, mapping, replacements, aliases, dimensions)


def orient_attachments(edges, roles):
    """applies_to has a declared modifier -> output direction, not guessed prose."""
    if not isinstance(edges, list):
        return edges  # The existing schema validator owns malformed values.
    result = []
    for edge in edges:
        if (isinstance(edge, list) and len(edge) == 3 and edge[2] == 'applies_to'
                and roles.get(edge[0]) == 'output' and roles.get(edge[1]) == 'modifier'):
            edge = [edge[1], edge[0], edge[2]]
        result.append(edge)
    return result


class ScopedRevision(MenuPartition):
    def compiled_decision(self, decision, source, context):
        decision = deepcopy(decision)
        split = decision.get('partition')
        if isinstance(split, dict) and isinstance(split.get('nodes'), list):
            old = [d for d in pending(self._partition_session['view'])
                   if d['owner_id'] == source['item_id']]
            owners = {}
            for row in split['nodes']:
                for did in row.get('pending', []):
                    owners.setdefault(did, []).append(row['id'])
            invalid = {d['decision_id']: owners.get(d['decision_id'], [])
                       for d in old if len(owners.get(d['decision_id'], [])) != 1}
            if invalid:
                raise ValueError('Each pending argument needs one owner; shared arguments '
                                 'need a single modifier attached to all users: ' + repr(invalid))
            represented = {d['dimension'] for d in old}
            for row in split['nodes']:
                row['questions'] = {k: v for k, v in row.get('questions', {}).items()
                                    if k not in represented}
            roles = {i: n['role'] for i, n in self._partition_session['view']['items'].items()}
            roles.update({n['id']: n.get('role', source['role']) for n in split['nodes']})
            roles['ask'] = decision['role']  # Parent compiler owns asked-node semantics.
            # The parent inventory already contains established dependencies
            # that the partition model may distribute to different children.
            # The parent decision is authoritative for `ask` and the base
            # partitioner copies its columns onto that node after this hook.
            # Put only unassigned dependencies there, so dependencies already
            # owned by a materialized remainder are not duplicated.
            asked_node = next(n for n in split['nodes'] if n.get('id') == 'ask')
            established = set(source.get('required_columns', []))
            child_columns = {c for row in split['nodes'] for c in row.get('columns', [])}
            unassigned = established - child_columns
            decision['required_columns'] = list(dict.fromkeys(
                decision.get('required_columns', []) + sorted(unassigned)))
            split['edges'] = orient_attachments(split.get('edges'), roles)
        return super().compiled_decision(decision, source, context)

    def compile_decision(self, call, payload, context, *, field, scope=False):
        captured = {}

        def scoped_call(stage, prompt, data):
            raw = call(stage, prompt + (REPLACEMENT_BOUNDARY if stage == 'permission_decision' else ''), data)
            if stage == 'permission_decision':
                if not isinstance(raw, dict) or 'replaceable_columns' not in raw:
                    raise ValueError('Permission decision needs replaceable_columns')
                raw = deepcopy(raw)
                captured['replaceable_columns'] = raw.pop('replaceable_columns')
            return raw

        decision = super().compile_decision(scoped_call, payload, context,
                                            field=field, scope=scope)
        values = captured.get('replaceable_columns', [])
        source_columns = set(payload['source']['required_columns'])
        purpose_columns = set(decision['required_columns'])
        if (not isinstance(values, list) or any(not isinstance(c, str) for c in values)
                or len(values) != len(set(values)) or not set(values) <= source_columns
                or not set(values) <= purpose_columns):
            raise ValueError('replaceable_columns must be unique dependencies of this purpose')
        decision['replaceable_columns'] = sorted(values)
        return decision

    @contextmanager
    def installed(self):
        def menu_dependencies(source, decision, dimension):
            return permission_menu_dependencies(source, decision, dimension)

        with super().installed(), patch.object(self.engine, 'menu_dependency_columns', menu_dependencies):
            yield self

    def commit_local(self, session, base, event, raw, context, editable):
        raw = deepcopy(raw)
        # Edges wholly outside the write boundary are immutable graph facts.
        # Some models echo the full visible graph during a local revision;
        # discard those echoes and let transaction() preserve the base edges.
        raw['edges'] = [edge for edge in raw.get('edges', [])
                        if not (isinstance(edge, list) and len(edge) == 3
                                and edge[0] in base['items'] and edge[1] in base['items']
                                and edge[0] not in editable and edge[1] not in editable)]
        turn = event.get('turn', {})
        target = event['target']
        if event['kind'] == 'answer' and not cancelling(event):
            decision = turn.get('scoped_permission_decision', {})
            if (decision.get('origin_dimension') == 'permission'
                    and turn.get('selection_mode') in ('return_fields', 'operand', 'complete')
                    and decision.get('replaceable_columns') is not None):
                replaceable = set(decision['replaceable_columns'])
                retained = set(decision.get('retained_columns', []))
                chosen = {c for option in turn.get('selected_options', [])
                          for c in option.get('required_columns', [])}
                if replaceable & retained:
                    raise ValueError('A dependency cannot be both replaceable and retained')
                if chosen & retained and decision.get('role') != 'modifier':
                    raise ValueError('A selected field cannot replace a common dependency')
                old = set(base['items'][target]['required_columns'])
                retired = (replaceable & old) - chosen
                # The menu fixed the overwrite boundary. The semantic updater
                # cannot turn it into an additive answer by carrying old columns.
                for row in raw.get('updates', {}).get(target, []):
                    if isinstance(row, dict) and isinstance(row.get('columns'), list):
                        row['columns'] = [c for c in row['columns'] if c not in retired]
                raw.setdefault('retire_columns', {})[target] = sorted(retired)

        if cancelling(event) and base['items'][target]['role'] == 'modifier':
            dependents = {e['target_id'] for e in base['relations']['edges']
                          if e['source_id'] == target and e['kind'] == 'applies_to'
                          and base['items'][e['target_id']]['role'] == 'output'}
            updates = raw.get('updates', {})
            omitted = dependents - set(updates)
            if omitted:
                raise ValueError('Cancelling a shared modifier requires revising its outputs: '
                                 + ', '.join(sorted(omitted)))
            for iid in dependents:
                rows = updates[iid]
                if not rows or any(row.get('role', 'output') != 'output' for row in rows):
                    raise ValueError('A shared modifier cancellation must preserve dependent outputs')

        # A dropped owner already retires its decisions. This is redundant
        # accounting, not a second semantic effect requiring another API call.
        if event['kind'] == 'removal' and isinstance(raw.get('question_changes'), dict):
            dropped = {i for i, rows in raw.get('updates', {}).items() if rows == []}
            by_id = {d['decision_id']: d for d in pending(base)}
            raw['question_changes'] = {
                did: effect for did, effect in raw['question_changes'].items()
                if not (did in by_id and by_id[did]['owner_id'] in dropped
                        and isinstance(effect, dict) and set(effect) == {'resolved'})}
        roles = {i: n['role'] for i, n in base['items'].items()}
        for parent, rows in raw.get('updates', {}).items():
            for row in rows:
                if isinstance(row, dict) and 'id' in row and parent in base['items']:
                    roles[row['id']] = ('modifier' if base['items'][parent]['role'] == 'modifier'
                                        else row.get('role', base['items'][parent]['role']))
        if 'edges' in raw:
            raw['edges'] = orient_attachments(raw['edges'], roles)
        return transaction(session, base, event, raw, context,
                           editable_ids=editable, keep_shared=event['kind'] == 'removal',
                           question_updater=argument_questions)

    def decision_input(self, prompt, payload):
        prompt, payload = super().decision_input(prompt, payload)
        payload = deepcopy(payload)
        base = self._partition_session['view']
        payload['query_context'] = {
            'nodes': [node_payload(n) for n in base['items'].values()],
            'edges': [[e[k] for k in ('source_id', 'target_id', 'kind')]
                      for e in base['relations']['edges']],
            'decisions': deepcopy(pending(base)),
        }
        return prompt + LOCATE, payload

    def edit_boundary(self, base, event):
        # Ordinary feedback changes the located unit, not its entire reachable
        # region. Cancellation may also orphan exclusively attached modifiers.
        if cancelling(event):
            editable = super().edit_boundary(base, event)
            target = event['target']
            if base['items'][target]['role'] == 'modifier':
                editable.update(e['target_id'] for e in base['relations']['edges']
                                if e['source_id'] == target and e['kind'] == 'applies_to'
                                and base['items'][e['target_id']]['role'] == 'output')
            return editable
        return {event['target']}

    def _transaction(self, call, session, base, event, context):
        where = locator(base, event)
        editable = self.edit_boundary(base, event)

        def located_call(stage, prompt, payload):
            payload = deepcopy(payload)
            active = payload.get('input', payload)
            active['revision_locator'] = deepcopy(where)
            active['read_only_query'] = {
                'nodes': [node_payload(n) for iid, n in base['items'].items()
                          if iid not in editable],
                'edges': [[e[k] for k in ('source_id', 'target_id', 'kind')]
                          for e in base['relations']['edges']],
            }
            return call(stage, prompt + REVISE, payload)

        result = super()._transaction(located_call, session, base, event, context)
        replacements = result['transaction_audit'][-1]['replacements']
        result.setdefault('revision_audit', []).append({
            'locator': where, 'editable_ids': sorted(editable),
            'replacements': deepcopy(replacements),
            'unchanged_ids': sorted(set(base['items']) - set(replacements)),
        })
        return result
