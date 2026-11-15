"""Reuse validated intermediates and pure schema work; always recheck access."""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import sys
from unittest.mock import patch

from ...engine import analysis, execution, hierarchical_questions
from .baseline import LightMainMethod
from .store import ArtifactCache, fingerprint

MODEL_STAGES = frozenset({'field_decision', 'scope_decision', 'permission_decision',
    'scope_menu_choices', 'hierarchical_field_filter', 'hierarchical_menu_groups',
    'hierarchical_field_labels', 'category_propagation'})


def aliases(stack, original, replacement):
    # Several engine modules import these functions by name. Patch every loaded
    # local alias and restore it on exit; dynamic imports see the patched source.
    for name, module in list(sys.modules.items()):
        if not name.startswith('clarification_main') or module is None:
            continue
        for attr, value in list(vars(module).items()):
            if value is original:
                stack.enter_context(patch.object(module, attr, replacement))


class CachedLightMain(LightMainMethod):
    def __init__(self, *, namespace, cache=None):
        super().__init__()
        if not namespace or cache is not None and cache.namespace != namespace:
            raise ValueError('Explicit matching model/runtime cache namespace required')
        self.artifacts = cache if cache is not None else ArtifactCache(namespace=namespace)
        self._guard = None

    def _scope(self, session, context):
        # Include all state rather than guessing that a neighboring answer is
        # irrelevant. Exclude only audit logs/transport bookkeeping, never meaning.
        return fingerprint({'context': context, 'session': {k: v for k, v in session.items()
            if k not in ('cache_audit', 'budget_audit', 'transaction_audit', 'reduction_audit')},
            'reductions': session.get('reduction_audit', [])})

    def _scoped(self, operation, call, session, context, *args, **kwargs):
        previous = self._guard
        self._guard = self._scope(session, context)
        try:
            return operation(call, session, *args, context=context, **kwargs)
        finally:
            self._guard = previous

    def prepare_menu(self, call, session, plan, context, max_options=5):
        return self._scoped(super().prepare_menu, call, session, context, plan, max_options=max_options)

    def analyze_session(self, call, session, context, reconstruct=None):
        return self._scoped(super().analyze_session, call, session, context, reconstruct=reconstruct)

    def commit_answer(self, call, session, plan, question, answer, context):
        return self._scoped(super().commit_answer, call, session, context, plan, question, answer)

    def validated(self, original, call, stage, prompt, payload, validator):
        if stage not in MODEL_STAGES or self._guard is None:
            return original(call, stage, prompt, payload, validator)
        inputs = {'guard': self._guard, 'stage': stage, 'prompt': prompt, 'payload': payload}
        found, raw = self.artifacts.get('validated_model', inputs)
        if found:
            audit = self.artifacts.audit[-1]
            try:
                # Re-run the CURRENT contract and any closed-over dependencies;
                # a cached semantic proposal is never a cached authorization.
                return validator(deepcopy(raw))
            except (ValueError, TypeError, KeyError):
                self.artifacts.discard('validated_model', inputs)
                audit['rejected_by_current_validator'] = True
        last = []
        def tracked(s, p, data):
            raw = call(s, p, data)
            last[:] = [deepcopy(raw)]
            return raw
        result = original(tracked, stage, prompt, payload, validator)
        if last:
            # Only publish after validation; failed first responses/failed
            # retries never poison the persistent artifact store.
            self.artifacts.put('validated_model', inputs, last[0], persist=True)
        return result

    @contextmanager
    def installed(self):
        original_indexes, original_graph = analysis.indexes, execution._authorized_graph
        original_validated = analysis.validated_call
        def cached_indexes(context):
            found, value = self.artifacts.get('schema_indexes', context)
            if not found:
                value = original_indexes(context)
                self.artifacts.put('schema_indexes', context, value)
            return value
        def cached_graph(context, allowed):
            # Normalize the mathematical set, never grant-sensitive values.
            inputs = {'context': context, 'allowed': sorted(set(allowed))}
            found, value = self.artifacts.get('authorized_graph', inputs)
            if not found:
                value = original_graph(context, allowed)
                self.artifacts.put('authorized_graph', inputs, value)
            return value
        def cached_validation(call, stage, prompt, payload, validator):
            return self.validated(original_validated, call, stage, prompt, payload, validator)
        with super().installed(), ExitStack() as stack:
            # Account separately for the lightweight baseline's existing reuse.
            # Do not claim it as a newly discovered persistent-cache hit.
            def observe_reuse(original, kind):
                def observed(call, *args, **kwargs):
                    called = []
                    def counted(*a, **kw):
                        called.append(True)
                        return call(*a, **kw)
                    result = original(counted, *args, **kwargs)
                    self.artifacts.audit.append({'kind': kind, 'hit': not bool(called),
                        'source': 'compiled_metadata' if not called else None})
                    return result
                return observed
            stack.enter_context(patch.object(hierarchical_questions, '_field_labels',
                observe_reuse(hierarchical_questions._field_labels, 'compiled_field_labels')))
            stack.enter_context(patch.object(hierarchical_questions, '_choices',
                observe_reuse(hierarchical_questions._choices, 'choices_without_api')))
            aliases(stack, original_indexes, cached_indexes)
            aliases(stack, original_graph, cached_graph)
            aliases(stack, original_validated, cached_validation)
            yield self
