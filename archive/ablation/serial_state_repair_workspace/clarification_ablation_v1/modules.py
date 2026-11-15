"""Coarse module ablations over the frozen v10 frontend.

No clarification: one unchanged initial analysis, no user callback, publish the
current residual wording without asserting that its open choices are resolved.
No selective pruning: retain policy detection, replace partial recovery with
whole-request refusal. This is NOT removal of the authorization gate.
"""
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch

from clarification_deferred_deletion.baseline import module, light
from clarification_useful_residual_v6.pool_method import PoolMethod

VERSION = 'main_module_ablation_v1'
VARIANTS = {'full': (True, True), 'no_clarification': (False, True),
            'no_pruning': (True, False)}


def blocked_ids(session):
    state = session.get('deferred_state') or {}
    return sorted(state.get('blocked_nodes') or {})


def has_reduction(session):
    return bool(blocked_ids(session) or session.get('removal_no_query')
                or session.get('module_whole_refusal')
                or any(e.get('after') == 'pruned' for e in
                       (session.get('obligation_pool') or {}).get('events', [])))


def unclarified_delivery(session):
    """An explicit ablation output boundary; never settles open decisions."""
    if session.get('contract_unresolved_reason') or session.get('deferred_review_rejection'):
        return dict(action='unresolved', text='', available_for_text2sql=False,
                    reason='initial_analysis_failed')
    view = session.get('view')
    if not view:
        return dict(action='unresolved', text='', available_for_text2sql=False,
                    reason='missing_initial_view')
    if not any(n.get('role') == 'output' for n in view['items'].values()):
        return dict(action='refuse', text='', available_for_text2sql=False,
                    reason='no_surviving_output')
    # Reuse the published node wording, including its attached conditions.
    # No completion call, answer invention, Gold access or guessed choice.
    text = (light.projection(view['items'], view.get('relations', {}).get('edges', []))
            if has_reduction(session) else session.get('original_request', ''))
    if not text.strip():
        return dict(action='unresolved', text='', available_for_text2sql=False,
                    reason='empty_initial_projection')
    return dict(action='deliver', text=text, available_for_text2sql=True,
                quality='unclarified', clarification_disabled=True,
                requires_sql_permission_check=True,
                reason='initial_residual_without_user_interaction')


class ModuleAblationMethod(PoolMethod):
    def __init__(self, *args, variant='full', **kwargs):
        if variant not in VARIANTS:
            raise ValueError('Unknown module ablation: '+variant)
        self.variant = variant
        self.interventions = []
        self.final_reduction = False
        super().__init__(*args, **kwargs)

    def audit(self):
        clarify, prune = VARIANTS[self.variant]
        return dict(version=VERSION, variant=self.variant,
                    clarification=clarify, selective_pruning=prune,
                    actual_policy_preserved=True, execution_feedback=False,
                    delivered_with_reduction=self.final_reduction,
                    interventions=self.interventions)

    def _complete_delivery(self, call, session, context):
        if self.variant == 'no_pruning' and has_reduction(session):
            return session
        return super()._complete_delivery(call, session, context)

    def _remove(self, call, session, basis, q, trigger, context):
        if self.variant != 'no_pruning':
            return super()._remove(call, session, basis, q, trigger, context)
        result = deepcopy(session)
        result['module_whole_refusal'] = dict(trigger=trigger)
        self.interventions.append(dict(hook='replace_removal_with_whole_refusal', trigger=trigger))
        return result

    def next_step(self, session):
        if self.variant == 'no_pruning' and has_reduction(session):
            return dict(action='refuse', reason='selective_pruning_disabled',
                        turn=len(session.get('turns', [])))
        return super().next_step(session)

    def render_session(self, call, session):
        if self.variant == 'no_pruning' and has_reduction(session):
            self.interventions.append(dict(hook='whole_request_refusal', blocked_ids=blocked_ids(session)))
            return dict(action='refuse', text='', available_for_text2sql=False,
                        reason='selective_pruning_disabled')
        return super().render_session(call, session)

    @contextmanager
    def installed(self):
        with super().installed():
            harness = module('engine.harness')
            original = harness.run_session

            def run(call, request, context, db_name, allowed, ask_user, **kwargs):
                episode = kwargs.get('episode')
                episode = {} if episode is None else episode
                if self.variant != 'no_clarification':
                    delivery = original(call, request, context, db_name, allowed,
                                        ask_user, **kwargs)
                else:
                    engine = module('engine.simple_session')
                    save = kwargs.get('save', lambda: None)
                    episode.update(request=request, snapshots=[], interactions=[],
                                   method=dict(name=VERSION, variant=self.variant))
                    session = engine.start_session(call, request, context, db_name, allowed,
                        analysis_mode='flat_compact', relation_context=False,
                        menu_notes=False, question_policy='heuristic', update_mode='propagation',
                        local_bundle=True, strict_menus=True)
                    episode['session'] = session
                    save()
                    session = engine.analyze_session(call, session, context)
                    episode['session'] = session
                    episode['snapshots'].append(deepcopy(session))
                    delivery = unclarified_delivery(session)
                    episode['delivery'] = delivery
                    self.interventions.append(dict(hook='skip_all_user_interaction',
                        user_turns=0, blocked_ids=blocked_ids(session),
                        publication=delivery.get('quality', delivery['action'])))
                    save()
                session = episode.get('session') or {}
                self.final_reduction = delivery['action'] == 'deliver' and has_reduction(session)
                return delivery

            with patch.object(harness, 'run_session', run):
                yield self
