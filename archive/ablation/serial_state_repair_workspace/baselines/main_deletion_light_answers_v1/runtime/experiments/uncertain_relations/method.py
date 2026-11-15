"""Represent uncertain modifier-to-output attachments as explicit questions.

This is an isolated experiment: it does not infer hidden graph truth or alter
node text. It asks only when a source-supported modifier could attach to several
outputs, then commits the user's selected relation edges deterministically.
"""
from copy import deepcopy
from contextlib import contextmanager
from unittest.mock import patch

from ..scoped_revision.method import ScopedRevision
from ...engine.analysis import validated_call

VERSION = 'uncertain_relations_v2_integrated'
RELATION_PROBE_PROMPT = '''Inspect the current graph for a relation whose scope is genuinely
uncertain: a modifier could apply to two or more current outputs, but the graph
does not establish the exact subset. Return JSON only:
{"uncertain_relations":[{"relation_id":"r1","modifier_id":"current ID",
"candidate_output_ids":["current output IDs"],"evidence":"brief reason"}]}.
Use current IDs only. Do not include established applies_to edges. Ground every
candidate in request wording or accepted answer history; shared schema columns
alone are not evidence. At most one relation group per modifier. Return an empty
array when none is evidenced. Do not change node text or dependencies.
'''


def prepare_relation_question(nodes, uncertain_relations):
    """Validate a proposed uncertainty set and render a business-facing menu.

    Each row has {relation_id, modifier_id, candidate_output_ids, evidence}.
    Evidence is explanatory provenance, not exposed as schema/access details.
    """
    by_id = {n['item_id']: n for n in nodes}
    questions = []
    seen = set()
    for row in uncertain_relations:
        if set(row) != {'relation_id', 'modifier_id', 'candidate_output_ids', 'evidence'}:
            raise ValueError('Invalid uncertain relation record')
        rid, mid, outputs = row['relation_id'], row['modifier_id'], row['candidate_output_ids']
        if (not isinstance(rid, str) or not rid or rid in seen or mid not in by_id
                or by_id[mid]['role'] != 'modifier' or not isinstance(outputs, list)
                or len(outputs) < 2 or len(set(outputs)) != len(outputs)
                or any(i not in by_id or by_id[i]['role'] != 'output' for i in outputs)
                or not isinstance(row['evidence'], str) or not row['evidence'].strip()):
            raise ValueError('Uncertain relations need one modifier and multiple current outputs')
        seen.add(rid)
        questions.append({
            'relation_id': rid, 'modifier_id': mid,
            'candidate_output_ids': outputs, 'evidence': row['evidence'],
            'question': f"Which requested parts should follow this condition: {by_id[mid]['requirement']}?",
            'options': [{'id': chr(65 + i), 'label': by_id[oid]['requirement'],
                         'output_id': oid} for i, oid in enumerate(outputs)],
            'multi_select': True,
        })
    return questions


def commit_relation_answer(graph, question, selected_option_ids):
    """Apply explicit user choices; unselected candidates receive no edge."""
    options = {o['id']: o for o in question['options']}
    if (not isinstance(selected_option_ids, list) or not selected_option_ids
            or len(set(selected_option_ids)) != len(selected_option_ids)
            or not set(selected_option_ids) <= set(options)):
        raise ValueError('Choose one or more offered relation options')
    new = deepcopy(graph)
    ids = {n['item_id'] for n in new['items']}
    if question['modifier_id'] not in ids or any(options[x]['output_id'] not in ids
                                                for x in selected_option_ids):
        raise ValueError('Relation answer refers to a stale graph')
    existing = {(e['source_id'], e['target_id'], e['kind'])
                for e in new.get('edges', [])}
    added = []
    for choice in selected_option_ids:
        edge = (question['modifier_id'], options[choice]['output_id'], 'applies_to')
        if edge not in existing:
            new.setdefault('edges', []).append(dict(zip(('source_id', 'target_id', 'kind'), edge)))
            existing.add(edge)
            added.append(edge)
    new.setdefault('resolved_relations', []).append({
        'relation_id': question['relation_id'],
        'selected_outputs': [options[x]['output_id'] for x in selected_option_ids],
        'reviewed_outputs': list(question['candidate_output_ids']),
        'unselected_outputs_confirmed_unattached': True,
    })
    return new, added


class UncertainRelationClarification(ScopedRevision):
    """Opt-in runner variant; unresolved attachments become business menus."""

    def _probe(self, call, session):
        nodes = [{'item_id': n['item_id'], 'role': n['role'],
                  'requirement': n['requirement']}
                 for n in session['view']['items'].values()]
        # A relation question is meaningful only after the graph exposes a
        # modifier and at least two independent outputs. Coarse compound nodes
        # must not trigger a speculative probe or an extra API call.
        if (sum(n['role'] == 'modifier' for n in nodes) < 1
                or sum(n['role'] == 'output' for n in nodes) < 2):
            return []
        payload = {
            'request': session['current_request'], 'nodes': nodes,
            'edges': [[e['source_id'], e['target_id'], e['kind']]
                      for e in session['view']['relations']['edges']],
            'accepted_history': [{'target': t.get('target_fragment'),
                'answer': t.get('answer', {}).get('text', ''),
                'selected': [o.get('text') for o in t.get('selected_options', [])]}
                for t in session.get('turns', [])
                if t['answer']['action'] in ('answer', 'choose')],
        }
        def check(raw):
            if (not isinstance(raw, dict) or set(raw) != {'uncertain_relations'}
                    or not isinstance(raw['uncertain_relations'], list)):
                raise ValueError('Expected uncertain_relations array')
            return prepare_relation_question(nodes, raw['uncertain_relations'])
        questions = validated_call(call, 'uncertain_relation_probe',
                                   RELATION_PROBE_PROMPT, payload, check)
        existing = {(e['source_id'], e['target_id'])
                    for e in session['view']['relations']['edges'] if e['kind'] == 'applies_to'}
        return [q for q in questions if not any(
            (q['modifier_id'], oid) in existing for oid in q['candidate_output_ids'])]

    def next_step(self, session):
        if session.get('uncertain_relations_pending'):
            return {'action': 'menu', 'turn': len(session['turns']),
                    'relation_id': session['uncertain_relations_pending'][0]['relation_id']}
        if session.get('uncertain_relations_blocked'):
            return {'action': 'unresolved', 'reason': 'uncertain_relation_not_resolved',
                    'turn': len(session['turns'])}
        return super().next_step(session)

    def prepare_relation_menu(self, session, plan):
        if plan != self.next_step(session):
            raise ValueError('Stale relation clarification plan')
        q = session['uncertain_relations_pending'][0]
        public = {'question': q['question'] + ' Select all that apply.',
                  'focus': q['question'], 'multi_select': True,
                  'granularity': 'relation',
                  'options': [{'id': o['id'], 'label': o['label']} for o in q['options']] +
                             [{'id': 'NONE', 'label': 'None of these meets my requirement'}]}
        new = deepcopy(session)
        new['pending'] = {'mode': 'uncertain_relation', 'relation_id': q['relation_id'],
                          'public': public, 'question': deepcopy(q)}
        return new, public

    def commit_relation_menu(self, session, plan, question, answer):
        if plan != self.next_step(session) or not session.get('pending'):
            raise ValueError('No active relation question')
        menu = session['pending']
        if question != menu['public'] or answer.get('action') not in ('choose', 'unanswerable'):
            raise ValueError('Invalid relation clarification answer')
        if answer['action'] == 'unanswerable':
            new = deepcopy(session)
            new['uncertain_relations_pending'] = []
            new['uncertain_relations_blocked'] = True
            new['pending'] = None
            return new
        choices = answer.get('choices', [])
        if not isinstance(choices, list) or not choices:
            raise ValueError('Choose one or more offered relationships')
        if 'NONE' in choices:
            if len(choices) != 1:
                raise ValueError('None-of-these is exclusive')
            new = deepcopy(session)
            new['uncertain_relations_pending'] = []
            new['uncertain_relations_blocked'] = True
            new.setdefault('relation_clarification_audit', []).append({
                'relation_id': menu['relation_id'], 'answer': 'none_of_these',
                'result': 'unresolved_no_candidate', 'update_model_calls': 0})
            new['pending'] = None
            return new
        graph = {'items': list(session['view']['items'].values()),
                 'edges': deepcopy(session['view']['relations']['edges'])}
        updated, added = commit_relation_answer(graph, menu['question'], choices)
        new = deepcopy(session)
        record = {'question': deepcopy(question), 'answer': deepcopy(answer),
                  'relation_id': menu['relation_id']}
        new.setdefault('public_interactions', []).append(record)
        new.setdefault('turns', []).append({**record, 'decision_key':
                                            'relation:' + menu['relation_id']})
        new['view']['relations']['edges'] = updated['edges']
        new['view']['relations']['edges'] = updated['edges']
        new.setdefault('relation_clarification_audit', []).append({
            'relation_id': menu['relation_id'], 'selected_option_ids': choices,
            'added_edges': [list(e) for e in added], 'update_model_calls': 0})
        new['uncertain_relations_pending'] = [q for q in new['uncertain_relations_pending']
                                               if q['relation_id'] != menu['relation_id']]
        new['pending'] = None
        return new

    @contextmanager
    def installed(self):
        original_next = self.engine.next_step
        original_prepare = self.engine.prepare_menu
        original_commit = self.engine.commit_answer
        original_analyze = self.engine.analyze_session
        with super().installed():
            def next_with_relations(session):
                if session.get('uncertain_relations_pending'):
                    return {'action': 'menu', 'turn': len(session['turns']),
                            'relation_id': session['uncertain_relations_pending'][0]['relation_id']}
                if session.get('uncertain_relations_blocked'):
                    return {'action': 'unresolved', 'reason': 'uncertain_relation_not_resolved',
                            'turn': len(session['turns'])}
                return original_next(session)

            def analyze_with_relations(call, session, context, reconstruct=None):
                updated = original_analyze(call, session, context, reconstruct)
                inventory = updated.get('view', {}).get('inventory_id')
                if inventory and updated.get('relation_probe_inventory') != inventory:
                    try:
                        questions = self._probe(call, updated)
                    except Exception as exc:
                        updated['uncertain_relations_blocked'] = True
                        updated.setdefault('relation_clarification_audit', []).append({
                            'result': 'probe_failed_closed', 'error': type(exc).__name__})
                    else:
                        updated['relation_probe_inventory'] = inventory
                        if questions:
                            updated['uncertain_relations_pending'] = questions
                return updated

            def prepare_with_relations(call, session, plan, context, max_options=5):
                if plan.get('relation_id'):
                    return self.prepare_relation_menu(session, plan)
                return original_prepare(call, session, plan, context, max_options)

            def commit_with_relations(call, session, plan, question, answer, context):
                if plan.get('relation_id'):
                    return self.commit_relation_menu(session, plan, question, answer)
                return original_commit(call, session, plan, question, answer, context)

            with patch.object(self.engine, 'next_step', next_with_relations), \
                 patch.object(self.engine, 'analyze_session', analyze_with_relations), \
                 patch.object(self.engine, 'prepare_menu', prepare_with_relations), \
                 patch.object(self.engine, 'commit_answer', commit_with_relations):
                yield self
