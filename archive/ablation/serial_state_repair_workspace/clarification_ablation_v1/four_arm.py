"""Executable four-arm interventions over the locked historical main method.

The historical prefix reconstruction remains an auxiliary experiment. These
variants execute the real lifecycle and never read historical future answers.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from unittest.mock import patch

from clarification_deferred_deletion.baseline import light, main, module
from clarification_useful_residual_v6.pool_method import PoolMethod
import clarification_deferred_v3_2.method as typed_method
from .modules import has_reduction, unclarified_delivery

VERSION = 'four_arm_executable_v1'
VARIANTS = {'full': (True, True), 'no_clarification': (False, True),
            'no_pruning': (True, False), 'clarify_then_prune': (True, True)}


def fingerprint(session):
    return hashlib.sha256(json.dumps(session, sort_keys=True, ensure_ascii=False,
                                    default=str).encode()).hexdigest()


class FourArmMethod(PoolMethod):
    def __init__(self, *args, variant='full', **kwargs):
        if variant not in VARIANTS:
            raise ValueError(variant)
        self.variant = variant
        self.phase = 'clarify' if variant == 'clarify_then_prune' else 'normal'
        self.interventions = []
        self.final_reduction = False
        super().__init__(*args, **kwargs)

    def audit(self):
        clarify, prune = VARIANTS[self.variant]
        return dict(version=VERSION, variant=self.variant, clarification=clarify,
                    selective_pruning=prune, phase=self.phase,
                    actual_policy_preserved=True, execution_feedback=False,
                    delivered_with_reduction=self.final_reduction,
                    interventions=self.interventions)

    def _business_question_focus(self, rows):
        # Policy probes remain in the decision ledger. They are not linguistic
        # gaps accepted by the grounding wire (scope/field/operation/value).
        if self.variant == 'clarify_then_prune' and self.phase == 'clarify':
            for row in rows:
                row['missing'] = [d for d in row.get('missing', []) if d != 'permission']
                if 'question_focus' in row:
                    row['question_focus'].pop('permission', None)

    def _refresh_focus(self, session):
        super()._refresh_focus(session)
        self._business_question_focus(session['view']['items'].values())

    def compile_decision(self, call, payload, context, *, field, scope=False):
        # The legacy permission-only fallback predates the typed/catalog wire
        # and omits mandatory clarification metadata. Serial scheduling makes
        # that dormant path reachable. Use the same current typed local
        # compiler as field/scope, preserving permission-specific validation.
        if not field and not scope:
            return light.LightweightUpdate.compile_decision(
                self, call, payload, context, field=field, scope=scope)
        return super().compile_decision(call, payload, context, field=field, scope=scope)

    def _whole_refusal(self, session, trigger):
        result = deepcopy(session)
        result['module_whole_refusal'] = dict(trigger=trigger)
        self.interventions.append(dict(hook='whole_request_refusal', trigger=trigger))
        return result

    def _decompose(self, call, session, context, none_event=None):
        if self.variant == 'no_pruning':
            return self._whole_refusal(session, 'required_decomposition')
        if self.variant == 'clarify_then_prune' and self.phase == 'clarify':
            # Keep every canonical contribution and all actual permission facts.
            # Menu authorization remains the original code's responsibility.
            self.interventions.append(dict(hook='postpone_permission_projection',
                                           turn=len(session.get('turns', []))))
            return session
        return super()._decompose(call, session, context, none_event)

    def _remove(self, call, session, basis, q, trigger, context):
        if self.variant == 'no_pruning':
            return self._whole_refusal(session, trigger)
        if self.variant == 'clarify_then_prune' and self.phase == 'clarify':
            result = deepcopy(basis)
            for key in ('turns', 'public_interactions'):
                result[key] = deepcopy(session.get(key, []))
            # A rejected/empty choice is recorded, not fabricated as an answer
            # and not used to delete a parent before clarification ends.
            event = dict(target=q['item']['item_id'], asked_ids=q.get('decision_ids', []),
                         question=q['focus'], trigger=trigger,
                         decision=deepcopy(q.get('scoped_permission_decision', {})))
            if not event['asked_ids']:
                result['contract_unresolved_reason'] = 'Serial removal has no decision identity'
                return result
            result.setdefault('serial_pending_removals', []).append(event)
            result['view']['deferred'] = list(dict.fromkeys(
                result['view'].get('deferred', []) + [q['key']]))
            result['pending'] = None
            result.get('menu_frontiers', {}).pop(q['key'], None)
            self.interventions.append(dict(hook='postpone_local_removal', event=deepcopy(event)))
            return result
        return super()._remove(call, session, basis, q, trigger, context)

    def _complete_delivery(self, call, session, context):
        if session.get('module_whole_refusal'):
            return session
        if self.variant == 'clarify_then_prune' and self.phase == 'clarify':
            # Binding completion/decomposition is performed in the second phase.
            return session
        return super()._complete_delivery(call, session, context)

    def next_step(self, session):
        if session.get('module_whole_refusal'):
            return dict(action='refuse', reason='selective_pruning_disabled',
                        turn=len(session.get('turns', [])))
        if self.variant == 'clarify_then_prune' and self.phase == 'clarify':
            if session.get('contract_unresolved_reason'):
                return dict(action='unresolved', reason=session['contract_unresolved_reason'])
            # Same frozen question ordering, authorization and navigation rules.
            # Terminal binding closure is deferred until after pruning.
            routing = deepcopy(session)
            # Permission probes belong to phase two. They must not suppress
            # the ordinary ambiguity questions on the same compound owner.
            if routing.get('view'):
                routing['view']['questions'] = [q for q in routing['view']['questions']
                                                if q['dimension'] != 'permission']
            return self.original['next_step'](routing)
        return super().next_step(session)

    def render_session(self, call, session):
        if session.get('module_whole_refusal'):
            return dict(action='refuse', text='', available_for_text2sql=False,
                        reason='selective_pruning_disabled')
        return super().render_session(call, session)

    def no_user_internal_work(self, call, session, context):
        """Run original internal menu work until a real user answer is needed."""
        seen = set()
        while not session.get('contract_unresolved_reason'):
            sig = fingerprint(session)
            if sig in seen:
                result = deepcopy(session)
                result['contract_unresolved_reason'] = 'No-user internal routing did not advance'
                return result
            seen.add(sig)
            plan = self.next_step(session)
            if plan['action'] != 'menu':
                # ask -> unknown business choice, terminal -> no more routing.
                return session
            updated, question = self.prepare_menu(call, session, plan, context)
            session = updated
            self.interventions.append(dict(hook='no_user_internal_menu',
                                           public_question_ready=question is not None))
            if question is not None:
                return session
        return session

    def finish_serial(self, call, session, context):
        self.phase = 'prune'
        current = deepcopy(session)
        events = current.pop('serial_pending_removals', [])
        self.interventions.append(dict(hook='enter_pruning_phase',
                                       user_turns=len(current.get('turns', []))))
        # Use the unchanged local pruning compiler; translate only code-owned
        # decision identities after answer-induced graph replacements.
        for event in events:
            pending = light.pending(current['view'])
            wanted = set(event['asked_ids'])
            matches = [d for d in pending if d['decision_id'] in wanted
                       or d.get('ancestor_decision_id') in wanted]
            if not matches:
                blocked = (current.get('deferred_state') or {}).get('blocked_decisions', [])
                if wanted <= {d['decision_id'] for d in blocked}:
                    continue
                current['contract_unresolved_reason'] = 'Serial removal lost its decision identity'
                return current
            owners = {d['owner_id'] for d in matches}
            if len(owners) != 1:
                current['contract_unresolved_reason'] = 'Serial removal has multiple current owners'
                return current
            mapped = dict(event, target=next(iter(owners)),
                          asked_ids=[d['decision_id'] for d in matches])
            current = super()._decompose(call, current, context, mapped)
            if current.get('contract_unresolved_reason'):
                return current
        # Clear routing deferrals, never the canonical unanswered decisions.
        if current.get('view'):
            current['view']['deferred'] = []
        current = super().analyze_session(call, current, context)
        return current

    @contextmanager
    def installed(self):
        with super().installed():
            harness = module('engine.harness')
            original = harness.run_session
            hierarchical = module('engine.hierarchical_questions')
            original_ready = hierarchical.ready
            original_questions = typed_method.apply_questions

            def apply_questions(*args):
                result = original_questions(*args)
                self._business_question_focus(args[3])
                return result

            def ready(session, question):
                if self.variant == 'clarify_then_prune' and self.phase == 'clarify':
                    routing = dict(session, permission_issues={})
                    return original_ready(routing, question)
                return original_ready(session, question)

            def run(call, request, context, db_name, allowed, ask_user, **kwargs):
                episode = kwargs.get('episode')
                episode = {} if episode is None else episode
                save = kwargs.get('save', lambda: None)
                if self.variant in ('full', 'no_pruning'):
                    result = original(call, request, context, db_name, allowed, ask_user, **kwargs)
                elif self.variant == 'no_clarification':
                    engine = module('engine.simple_session')
                    episode.update(request=request, snapshots=[], interactions=[],
                                   method=dict(name=VERSION, variant=self.variant))
                    session = engine.start_session(call, request, context, db_name, allowed,
                        analysis_mode='flat_compact', relation_context=False, menu_notes=False,
                        question_policy='heuristic', update_mode='propagation',
                        local_bundle=True, strict_menus=True)
                    episode['session'] = session
                    save()
                    session = engine.analyze_session(call, session, context)
                    session = self.no_user_internal_work(call, session, context)
                    episode['session'] = session
                    episode['snapshots'].append(deepcopy(session))
                    result = unclarified_delivery(session)
                    episode['delivery'] = result
                    self.interventions.append(dict(hook='skip_user_calls_keep_internal_pruning',
                                                   user_turns=0))
                    save()
                else:
                    # Original harness handles all questions and answers. Its
                    # terminal render is intercepted to run the pruning phase.
                    # No user callback can occur after this phase transition.
                    def finish(actual_call, session):
                        if session.get('contract_unresolved_reason'):
                            return PoolMethod.render_session(self, actual_call, session)
                        session = self.finish_serial(actual_call, session, context)
                        episode['session'] = session
                        episode['snapshots'].append(deepcopy(session))
                        plan = self.next_step(session)
                        if plan['action'] in ('ask', 'menu', 'analyze'):
                            return dict(action='unresolved', text='', available_for_text2sql=False,
                                        reason='serial_residual_requires_further_clarification')
                        return PoolMethod.render_session(self, actual_call, session)
                    with patch.object(self.engine, 'render_session', finish):
                        result = original(call, request, context, db_name, allowed, ask_user, **kwargs)
                self.final_reduction = result['action'] == 'deliver' and has_reduction(
                    episode.get('session') or {})
                return result

            with patch.object(harness, 'run_session', run), patch.object(hierarchical, 'ready', ready), \
                 patch.object(typed_method, 'apply_questions', apply_questions):
                yield self
