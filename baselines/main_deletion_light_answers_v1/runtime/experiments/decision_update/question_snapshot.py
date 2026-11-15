"""Opt-in local question snapshots with two interaction modes, not four gap types.

The model owns current unresolved meaning. Code replaces reviewed question lists
atomically, keeps untouched owners, and delegates scope/field routing to the
existing business-decision compiler. No cross-version semantic matching.
"""
from copy import deepcopy
from functools import partial

from ... import method as main
from ...engine import analysis, session_update
from .hybrid import HybridUpdate
from .ledger import pending
from .lightweight import (transaction, region, node_payload, event_payload,
                          protected_outputs, schema_payload, objective_kind)


SOURCE = '''
Give every fragment an open_choices array (empty when none). Each entry is
{"dimension":"menu|detail","span":"exact quote inside this fragment",
"question":"one question about one unresolved purpose"}.
menu: choose a business entity, information category, returned field or filter
operand. detail: specify a quantity, subset/coverage, literal value, time window
or calculation using those business operands. You need not distinguish entity
from field, or operation from value. An unspecified subset does not imply all.
Selecting members/values USING an identified property is detail; menu selects
the property itself. Combine alternative ways to specify the same subset choice.
Read the user's language, not a likely database interpretation. Keep independent
unanswered purposes, without inventing requirements or duplicating a question.
'''

ANSWER = '''Update the editable local requirements using the current answer.
Return JSON only:
{"updates":{"f1":[{"id":"p1","text":"current requirement","columns":["t.c"],
"questions":[{"mode":"menu|detail","question":"one remaining question"}]}]},
"edges":[["p1","f2","applies_to"]],"retire_columns":{"f1":["t.old"]}}.
Only updates is required. Omit unchanged parents. Each child needs id, text,
columns and questions (use [] when clear). Optional role, kind, tables and
candidates inherit the existing interface; local IDs are aliases, not old IDs.

For EACH changed child, questions is its COMPLETE current unresolved list.
Replace the old list; do not submit question_changes, pending IDs or resolutions.
Old questions are review context, not permanent tasks. The actual answer may
resolve several related questions, only part of a question, or introduce a new
dependency. Keep every independent unanswered meaning in BOTH text and questions.
Selecting fields does not supply quantities, subsets, thresholds or formulas.
Do not change unspecified coverage to 'all', 'every' or 'each'. Do not keep a
generic field question after that purpose's requested fields were fully chosen.

Two modes only: menu chooses business entities, categories, returned fields or
filter operands; detail asks literal values, subset/quantity, time windows or
calculation methods. No scope/field or operation/value classification is needed.
Separate different purposes; do not list the same uncertainty in both modes.
Menu selects a schema-backed business property/operand. Selecting which members
or values to include USING an already chosen property is detail, not a renewed
field choice. Combine alternative descriptions of one unresolved subset choice
(such as a number or named members) into one question; keep independent choices.
If a calculation requires an as-yet-unchosen business operand, first include a
menu question for that operand; do not obtain an unchosen field via open text.

Change only editable_ids and meanings actually affected by this answer. Keep
selected columns in the answered purpose. Declare all determined operands and
join keys with literal schema IDs; candidates are alternatives, not commitments.
retire_columns lists old dependencies replaced by this field/business choice.
One-child updates inherit unretired dependencies; splits must distribute them.
Preserve independent outputs and filters with their roles and subjects. A filter
operand is not an extra output. Split only independently actionable meanings.
Output-specific calculations need their own qualifications; shared modifiers
must express the same constraint for every target. Optional role/kind/tables
inherit the parent. Update tables when the actual subject/attachment changes.
Return edges touching revised/split nodes: applies_to source qualifies target;
context source relies on target. Unchanged and same-role one-to-one attachments
are kept by code. Boundary nodes are read-only. Preserve AND/OR, negation,
comparisons and unresolved intent. No SQL, permissions or external workarounds.
'''

MODE = {'menu': 'field', 'detail': 'value'}


def checked_questions(value):
    if not isinstance(value, list):
        raise ValueError('questions needs a complete array, possibly []')
    result = []
    for q in value:
        if (not isinstance(q, dict) or set(q) != {'mode', 'question'}
                or q.get('mode') not in MODE or not isinstance(q.get('question'), str)
                or not q['question'].strip()):
            raise ValueError('Each question needs menu/detail mode and nonempty question')
        row = {'mode': q['mode'], 'question': q['question'].strip()}
        if row not in result:
            result.append(row)
    return result


def replace_questions(base, event, raw, mapping, replacements, aliases, dimensions, *, snapshots):
    """No old-question merging inside updated owners; keep outside owners exactly."""
    old = pending(base)
    carried = [d for d in old if d['owner_id'] not in replacements]
    serial = base.get('progressive', {}).get('next_decision_id', 1)
    used = {d['decision_id'] for d in old}
    inverse = {v: k for k, v in aliases.items()}
    for row in mapping:
        focus = {}
        for q in snapshots[inverse[row['item_id']]]:
            while f'd{serial}' in used:
                serial += 1
            dim = MODE[q['mode']]
            carried.append({'decision_id': f'd{serial}', 'owner_id': row['item_id'],
                            'dimension': dim, 'question': q['question'],
                            'source_span': row['requirement']})
            used.add(f'd{serial}')
            serial += 1
            focus.setdefault(dim, []).append(q['question'])
        row['missing'] = list(focus)
        row['question_focus'] = {d: ' '.join(qs) for d, qs in focus.items()}
    audit = [{'effect': 'replaced_local_snapshot', 'parents': list(replacements),
              'old_count': sum(d['owner_id'] in replacements for d in old),
              'new_count': sum(d['owner_id'] in inverse for d in carried)}]
    return carried, serial, audit


def commit_snapshot(session, base, event, raw, context):
    raw = deepcopy(raw)
    if not isinstance(raw, dict) or not isinstance(raw.get('updates'), dict):
        raise ValueError('Expected updates mapping')
    if 'question_changes' in raw:
        raise ValueError('Use complete questions, not incremental question_changes')
    snapshots = {}
    for rows in raw['updates'].values():
        if not isinstance(rows, list):
            raise ValueError('Updated children must be an array')
        for row in rows:
            questions = checked_questions(row.get('questions'))
            snapshots[row['id']] = questions
            focus = {}
            for q in questions:
                focus.setdefault(MODE[q['mode']], []).append(q['question'])
            row['questions'] = {d: ' '.join(qs) for d, qs in focus.items()}
    return transaction(session, base, event, raw, context, compound_answers=True,
                       question_updater=partial(replace_questions, snapshots=snapshots))


class QuestionSnapshotUpdate(HybridUpdate):
    """Hybrid deletion stays fixed; only initial/answer uncertainty annotation changes."""

    def analyze_flat(self, call, payload, context):
        if self._base is not None:
            return super().analyze_flat(call, payload, context)

        def coarse(stage, prompt, data):
            if stage.startswith('progressive_coarse'):
                prompt = prompt.replace(main.SOURCE_CHOICES, SOURCE)
            raw = call(stage, prompt, data)
            if stage.startswith('progressive_coarse'):
                raw = deepcopy(raw)
                for f in raw.get('fragments', []):
                    for q in f.get('open_choices', []):
                        if q.get('dimension') in MODE:
                            q['dimension'] = MODE[q['dimension']]
            return raw

        # Bypass light's partitive regex injection and inherited-question setup.
        result = main.MainMethod.analyze_flat(self, coarse, payload, context)
        info = result[4]['progressive']
        ledger, serial = [], 1
        for row in result[2]['items']:
            iid = row['item_id']
            row['kind'] = objective_kind(result[1][iid]['role'], row['kind'],
                                         [c for b in row['bindings'] for c in b], row['question_focus'])
            entries = list(info.get('source_open_choices', {}).get(iid, []))
            entries += [{'dimension': d, 'span': result[1][iid]['span'],
                         'question': row['question_focus'][d]} for d in row['missing']
                        if not any(e['dimension'] == d for e in entries)]
            focus = {}
            for e in entries:
                dim = 'field' if e['dimension'] in ('scope', 'field') else 'value'
                question = e['question']
                if any(d['owner_id'] == iid and d['dimension'] == dim and d['question'] == question for d in ledger):
                    continue
                ledger.append({'decision_id': f'd{serial}', 'owner_id': iid,
                               'dimension': dim, 'source_span': e['span'], 'question': question})
                serial += 1
                focus.setdefault(dim, []).append(question)
            row['missing'] = list(focus)
            row['question_focus'] = {d: ' '.join(qs) for d, qs in focus.items()}
        info.update(decision_ledger=ledger, next_decision_id=serial,
                    question_policy='answer_snapshot', source_open_choices={})
        return result

    def _transaction(self, call, session, base, event, context):
        if event['kind'] != 'answer':
            return super()._transaction(call, session, base, event, context)
        editable = region(base, event)
        edges = [e for e in base['relations']['edges'] if {e['source_id'], e['target_id']} & editable]
        visible = editable | {e[k] for e in edges for k in ('source_id', 'target_id')}
        nodes = []
        for iid, n in base['items'].items():
            if iid not in visible:
                continue
            item = node_payload(n)
            item.pop('questions')
            item['previous_unresolved'] = [
                {'mode': 'menu' if d['dimension'] in ('scope', 'field') else 'detail', 'question': d['question']}
                for d in pending(base) if d['owner_id'] == iid]
            nodes.append(item)
        payload = {'event': event_payload(event), 'editable_ids': sorted(editable),
                   'preserve_outputs': sorted(protected_outputs(base, event) & editable),
                   'nodes': nodes, 'edges': [[e[k] for k in ('source_id', 'target_id', 'kind')] for e in edges],
                   'schema': schema_payload(context)}
        result = analysis.validated_call(call, 'answer_question_snapshot', ANSWER, payload,
            lambda raw: commit_snapshot(session, base, event, raw, context))
        return self._attach_plan(result)
