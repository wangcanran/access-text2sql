"""One deletion entry; reuse snapshot answers and the existing authorization code."""
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch

from ...engine import simple_session
from ..decision_update.question_snapshot import QuestionSnapshotUpdate
from .compiler import GUIDANCE
from .removal import remove_transaction
from .answer import permission_answer

VERSION = 'menu_bound_purpose_v1'
_base_dependencies = simple_session.menu_dependency_columns


def menu_dependencies(source, decision, dimension):
    # Being part of the asked purpose does NOT make a dependency replaceable.
    # Missing decomposition is conservative, never an implicit authorization.
    if dimension == 'permission':
        required = set(source['required_columns']) | set(decision['required_columns'])
        return sorted(required - set(decision.get('replaceable_columns', [])))
    return _base_dependencies(source, decision, dimension)


class PurposeWithdrawal(QuestionSnapshotUpdate):
    def compiled_decision(self, decision, source, context):
        decision = deepcopy(super().compiled_decision(decision, source, context))
        values = decision.get('replaceable_columns', [])
        canonical = {c.casefold(): c for c in decision['required_columns']}
        if (not isinstance(values, list) or any(not isinstance(c, str)
                or c.casefold() not in canonical for c in values)):
            raise ValueError('replaceable_columns must be a subset of purpose required_columns')
        decision['replaceable_columns'] = sorted({canonical[c.casefold()] for c in values})
        return decision

    def decision_input(self, prompt, payload):
        prompt, payload = super().decision_input(prompt, payload)
        return prompt + '\n' + GUIDANCE, payload

    @contextmanager
    def installed(self):
        # Scope the experiment's menu semantics; restore the baseline on exit,
        # including exceptions. The runner executes cases serially.
        with super().installed(), patch.object(simple_session, 'menu_dependency_columns', menu_dependencies):
            yield self

    def _transaction(self, call, session, base, event, context):
        if event['kind'] == 'removal':
            return remove_transaction(call, session, base, event, context)
        turn = event.get('turn', {})
        if (event['kind'] == 'answer' and turn.get('permission_field_choice')
                and turn.get('scoped_permission_decision')):
            return self._attach_plan(permission_answer(call, session, base, event, context))
        return super()._transaction(call, session, base, event, context)

    def _remove(self, call, session, basis, q, trigger, context):
        # Deliberately do not call HybridUpdate._remove, code_deletable,
        # deletion_patch or the old subtractive snapshot router.
        base = basis['view']
        target = q['item']['item_id']
        if target not in base['items']:
            raise ValueError('Removal requires a current node identity')
        decision = deepcopy(q.get('scoped_permission_decision', {}))
        if not decision.get('requirement'):
            raise ValueError('Withdrawal requires the local purpose compiled for this menu')
        event = {'kind': 'removal', 'target': target, 'dimension': q['dimension'],
                 'question': q['focus'], 'decision': decision}
        result = self._transaction(call, session, base, event, context)
        audit = result['transaction_audit'][-1]
        audit['execution'] = VERSION
        replacements = audit['replacements']
        result.setdefault('reduction_audit', []).append({
            'after_turn': len(session['turns']), 'trigger': trigger,
            'target_fragment': q['item']['span'], 'target_role': q['item']['role'],
            'question': q['focus'], 'asked_dimension': q['dimension'],
            'previous_request': session['current_request'], 'request': result['current_request'],
            'source_inventory': base['inventory_id'], 'method': VERSION,
            'scoped_permission_decision': decision, 'analysis_mode': 'menu_bound_local_snapshot',
            'receipt': {'request': result['current_request'],
                'drop_ids': [p for p, children in replacements.items() if not children],
                'revise_ids': [p for p, children in replacements.items() if children]}})
        return result
