"""One semantic writer, disposable graph views, code-only menu navigation.

Inheritance reuses the stateless business-decision compiler/menu preparation,
not the inherited installation, answer transactions or rendering pipeline.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from unittest.mock import patch

from ... import method as main
from ...engine import (analysis, answer_effects, category_propagation,
                       hierarchical_questions as hierarchy, session_update,
                       simple_session as engine)
from ...objective_contract import has_query_objective
from ..decision_update.lightweight import reuse_field_labels, reuse_scope_choices
from ..decision_update.question_snapshot import QuestionSnapshotUpdate
from ..purpose_withdrawal.method import PurposeWithdrawal, menu_dependencies

VERSION = 'text_state_v1'

REVISE = '''Apply one local interaction to current_request. Return only JSON
{"request":"complete updated natural-language database query"}.
current_request is the sole active intent. The local purpose and public question
locate this interaction; they are not new requirements. No SQL or explanation.
For answer, incorporate only the actual answer and selected business options.
A completed field multiselection specifies the fields for the asked purpose;
unselected alternatives are not additional requests. Preserve independent
unanswered quantities, coverage, values, calculations and unrelated purposes.
For cancel, remove the asked local purpose, not its entire compound container.
Cancel is an explicit protocol instruction, even if the original intent wanted
that information. Remove a qualification only if none of its targets survive.
Keep shared conditions for surviving targets, and keep independently requested
values and their subject/associations when a filter or calculation is cancelled.
Never promote filter subjects or operands into additional returned outputs.
Keep AND/OR, negation, comparisons and remaining uncertainty. Do not invent
fields, choose missing parameters, restore removed requirements or suggest
external workarounds. Write a query request, not assistant questions or a report
of the dialogue. Return empty request only when no query objective remains.
'''


def checked_request(raw):
    if (not isinstance(raw, dict) or set(raw) != {'request'}
            or not isinstance(raw['request'], str)):
        raise ValueError('Expected one request string')
    return raw['request'].strip()


class TextState(PurposeWithdrawal):
    """All semantic events follow edit text -> fresh analysis -> atomic commit."""

    def __init__(self):
        super().__init__()
        self._start = engine.start_session

    def start_session(self, call, text, context, db_name, allowed, **options):
        options.update(analysis_mode='flat_compact', update_mode='full',
                       local_bundle=False, relation_context=False,
                       question_policy='heuristic', strict_menus=True)
        result = self._start(call, text, context, db_name, allowed, **options)
        result.update(architecture=VERSION, text_revision=0)
        return result

    def _history(self, session):
        # Menu compilation uses the current request/view, never removed history.
        return {'turns': []}

    def fresh_view(self, call, session, context):
        new = deepcopy(session)
        request = new['current_request']
        if request:
            payload = {'db_name': new['db_name'],
                'schema': {k: context[k] for k in ('tables', 'foreign_keys')},
                'column_meanings': context.get('column_language', {}),
                'current_request': request, 'public_conversation': {'turns': []}}
            # Explicit initial-analysis entry: no old graph or update event.
            frame, fragments, mapping, edges, metadata = QuestionSnapshotUpdate.analyze_flat(
                self, call, payload, context)
            items = {}
            for row in mapping['items']:
                iid = row['item_id']
                columns = sorted({c for binding in row['bindings'] for c in binding})
                items[iid] = {**row, **fragments[iid], 'required_columns': columns,
                    'denied_columns': sorted(set(columns) - set(new['allowed']))}
            frame_json = frame.to_json()
            info = deepcopy(metadata['progressive'])
        else:
            items, edges, metadata = {}, {'edges': []}, {}
            frame_json = {'original_text': '', 'outputs': [], 'modifiers': []}
            info = {'decision_ledger': [], 'question_policy': 'answer_snapshot'}
        new.update(pending=None, menu_frontiers={}, menu_blockers={}, permission_issues={})
        new['view'] = {'items': items, 'frame': frame_json, 'relations': edges,
            'progressive': info,
            'inventory_id': engine.signature(VERSION, new.get('text_revision', 0), request),
            'questions': [], 'deferred': [], 'candidate_audit': [], 'shared_decisions': [],
            'segmentation': {'mode': VERSION, 'inventory': metadata},
            'update_audit': {'mode': 'fresh_from_current_text'}}
        new['removal_no_query'] = not request
        new['no_detected_objective'] = bool(request) and not has_query_objective(items.values())
        # No lineage/evidence reconciliation across revisions: finish derives
        # permissions from this inventory and the code policy alone.
        new = session_update.finish_view(new, context)
        main.check_active_state(new)
        return new

    def analyze_session(self, call, session, context, reconstruct=None):
        if session.get('view') is not None:
            return session
        return self.fresh_view(call, session, context)

    def next_step(self, session):
        turn = len(session['turns'])
        if session.get('removal_no_query'):
            return {'action': 'refuse', 'reason': 'no_remaining_query_objective', 'turn': turn}
        if session.get('text_update_stalled') or session.get('no_detected_objective'):
            return {'action': 'unresolved', 'reason': 'text_update_no_progress' if
                session.get('text_update_stalled') else 'no_detected_query_objective', 'turn': turn}
        if self._preparing and session.get('view'):
            inventory, count, key = self._preparing
            if (session['view']['inventory_id'] == inventory and turn == count
                    and key not in session['view']['deferred']):
                q = next((q for q in session['view']['questions'] if q['key'] == key), None)
                if q is not None and hierarchy.ready(session, q):
                    return {'action': 'menu', 'turn': turn, 'question_id': key}
        plan = self.original['next_step'](session)
        if plan['action'] == 'deliver' and (session['view']['questions'] or
                any(n['missing'] or n.get('status') == 'unresolved'
                    for n in session['view']['items'].values())):
            return {'action': 'unresolved', 'reason': 'unrouted_current_decision', 'turn': turn}
        return plan

    def revise(self, call, session, q, event, context):
        decision = q.get('scoped_permission_decision', {})
        payload = {'current_request': session['current_request'], 'interaction': {
            'kind': event['kind'],
            'purpose': decision.get('requirement', q['focus']),
            'source_fragment': q['item']['span'],
            'role': decision.get('role', q['item']['role']),
            'question': deepcopy(event.get('question', {'question': q['focus']})),
            'answer': deepcopy(event.get('answer')),
            'selected_options': deepcopy(event.get('selected_options', []))}}
        request = analysis.validated_call(call, 'text_state_revise', REVISE, payload, checked_request)
        if not request and event['kind'] != 'cancel':
            raise ValueError('An ordinary answer cannot silently cancel the whole request')
        new = deepcopy(session)
        new.update(current_request=request, text_revision=session.get('text_revision', 0) + 1)
        # Do not retry an unchanged decision forever or count it as success.
        new['text_update_stalled'] = request == session['current_request']
        new.setdefault('text_events', []).append({**deepcopy(payload['interaction']),
            'before': session['current_request'], 'after': request})
        if event['kind'] == 'cancel':
            new.setdefault('reduction_audit', []).append({
                'after_turn': len(new['turns']), 'trigger': event.get('trigger', 'local_rejection'),
                'target_fragment': q['item']['span'], 'target_role': payload['interaction']['role'],
                'question': q['focus'], 'asked_dimension': q['dimension'],
                'previous_request': session['current_request'], 'request': request, 'method': VERSION})
        # Either both the text and its new view commit, or neither does.
        return self.fresh_view(call, new, context)

    def _remove(self, call, session, basis, q, trigger, context):
        return self.revise(call, session, q, {'kind': 'cancel', 'trigger': trigger}, context)

    def commit_answer(self, call, session, plan, question, answer, context):
        q = engine.decision(session, plan)
        if answer.get('action') in ('submit', 'refuse'):
            raise ValueError('Use choose, answer or unanswerable for a local decision')
        # Reuse code validation of public question, menu IDs, policy and joins.
        new = self.original['commit_answer'](call, session, plan, question, answer, context)
        if len(new['turns']) == len(session['turns']):
            return new  # Categories/queued branches change only the frontier.
        turn = new['turns'][-1]
        cancel = turn.pop('menu_rejected', False) or turn['answer']['action'] == 'unanswerable'
        turn['scoped_permission_decision'] = deepcopy(q.get('scoped_permission_decision', {}))
        return self.revise(call, new, q, {'kind': 'cancel' if cancel else 'answer',
            'question': question, 'answer': turn['answer'],
            'selected_options': turn.get('selected_options', [])}, context)

    def render_session(self, call, session):
        # The base renderer returns current_request and never calls the model.
        result = self.original['render_session'](call, session)
        result['method'] = VERSION
        return result

    @contextmanager
    def installed(self):
        """Do not install any inherited graph updater or lineage hooks."""
        choices, labels, ready = hierarchy._choices, hierarchy._field_labels, hierarchy.ready

        def scoped_choices(call, session, q, context):
            if q.get('scoped_permission_decision') and q['dimension'] != 'scope':
                if not q['item']['field_candidates']:
                    return [], True
                q = {**q, 'dimension': 'field'}
            return choices(call, session, q, context)

        def local_ready(session, q):
            if q.get('scoped_permission_decision') and q['dimension'] == 'field':
                return 'scope' not in q['item']['missing']
            return ready(session, q)

        with ExitStack() as stack:
            for name in self.original:
                stack.enter_context(patch.object(engine, name, getattr(self, name)))
            for module, name, replacement in (
                (engine, 'start_session', self.start_session),
                (engine, 'public_history', lambda s: {
                    'original_request': s['current_request'], 'turns': []}),
                (engine, 'menu_dependency_columns', menu_dependencies),
                (category_propagation, 'propagate', lambda call, session, *a: session),
                (answer_effects, 'resolve_permission_choice', lambda *a: None),
                (hierarchy, 'ready', local_ready),
                (hierarchy, '_choices', lambda *a: reuse_scope_choices(scoped_choices, *a)),
                (hierarchy, '_field_labels', lambda *a: reuse_field_labels(labels, *a))):
                stack.enter_context(patch.object(module, name, replacement))
            yield self
