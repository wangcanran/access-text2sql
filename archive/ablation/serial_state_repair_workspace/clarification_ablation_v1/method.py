"""A 2x2 intervention on deterministic propagation and terminal closure gates.

This does not remove model dependency reasoning, contribution lineage, grounding
attempts, direct permission checks, or ordinary pending-question checks.
"""
from collections import Counter
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from unittest.mock import patch

from clarification_deferred_deletion import graph
import clarification_deferred_deletion.method as deletion
from clarification_contract_v5 import bindings
import clarification_contract_v5.method as contracts
from clarification_useful_residual_v6 import obligations, pool_decomposition
from clarification_useful_residual_v6.pool_method import PoolMethod, _PATCH_LOCK

VERSION = 'main_ablation_v1_deterministic_propagation_terminal_gate'
VARIANTS = {
    'full': (True, True),
    'no_dependency': (False, True),
    'no_obligation_gate': (True, False),
    'no_both': (False, False),
}


def direct_columns(plan):
    return {n['id']: set(n['columns']) for n in plan['nodes']}


def direct_closure(plan, allowed, seeds=None):
    """Keep direct exclusions and orphan cleanup; omit lost-provider cascade."""
    allowed = set(allowed)
    nodes = {n['id']: n for n in plan['nodes']}
    dropped = deepcopy(seeds or {})
    for iid, node in nodes.items():
        denied = sorted(set(node['columns']) - allowed)
        if denied:
            dropped[iid] = dict(rule='denied_dependency', columns=denied)
        elif node['candidates'] and not set(node['candidates']) & allowed:
            dropped[iid] = dict(rule='empty_authorized_field_domain',
                                columns=sorted(node['candidates']))
    needed = {i for i, n in nodes.items() if n['role'] == 'output' and i not in dropped}
    while True:
        size = len(needed)
        for a, b, kind in plan['edges']:
            if a in dropped or b in dropped:
                continue
            if kind == 'requires' and a in needed:
                needed.add(b)
            if kind == 'applies_to' and b in needed:
                needed.add(a)
        if len(needed) == size:
            break
    for iid, node in nodes.items():
        if node['role'] == 'modifier' and iid not in needed and iid not in dropped:
            dropped[iid] = dict(rule='no_surviving_output_consumer')
    return dropped


class AblationMethod(PoolMethod):
    def __init__(self, *args, variant='full', **kwargs):
        if variant not in VARIANTS:
            raise ValueError('Unknown ablation: ' + variant)
        self.variant = variant
        self.hook_counts = Counter()
        self.hook_events = []
        super().__init__(*args, **kwargs)

    def audit(self):
        dep, gate = VARIANTS[self.variant]
        return dict(version=VERSION, variant=self.variant,
                    deterministic_dependency_propagation=dep,
                    pending_obligation_terminal_gate=gate,
                    hook_counts=dict(self.hook_counts), hook_events=self.hook_events)

    @contextmanager
    def installed(self):
        dep, gate = VARIANTS[self.variant]
        with _PATCH_LOCK, ExitStack() as stack:
            def counted(name, fn):
                def invoke(*args, **kwargs):
                    self.hook_counts[name] += 1
                    return fn(*args, **kwargs)
                return invoke

            if not dep:
                original_columns, original_closure = graph.required_columns, graph.closure

                def columns_without_propagation(plan):
                    result = direct_columns(plan)
                    baseline = original_columns(plan)
                    omitted = {i: sorted(baseline[i] - result[i]) for i in result
                               if baseline[i] != result[i]}
                    if omitted:
                        self.hook_events.append(dict(hook='dependency_columns_omitted', nodes=omitted))
                    return result

                def closure_without_propagation(plan, allowed, seeds=None):
                    result = direct_closure(plan, allowed, seeds)
                    baseline = original_closure(plan, allowed, seeds)
                    omitted = sorted(set(baseline) - set(result))
                    if omitted:
                        self.hook_events.append(dict(hook='dependency_cascade_omitted', nodes=omitted))
                    return result

                for owner in (graph, deletion):
                    stack.enter_context(patch.object(owner, 'required_columns',
                        counted('direct_columns', columns_without_propagation)))
                    stack.enter_context(patch.object(owner, 'closure',
                        counted('direct_closure', closure_without_propagation)))
                for owner in (bindings, contracts, pool_decomposition):
                    stack.enter_context(patch.object(owner, 'derive_requires_operand_claims',
                        counted('skip_operand_propagation', lambda nodes, edges: nodes)))
            if not gate:
                def filter_gate(original, code, label):
                    def invoke(session):
                        self.hook_counts[label] += 1
                        problems = original(session)
                        removed = [p for p in problems if p.get('code') == code]
                        if removed:
                            self.hook_counts[label + '_suppressed'] += len(removed)
                            self.hook_events.append(dict(hook=label, issues=deepcopy(removed),
                                turn=len(session.get('turns', []))))
                        return [p for p in problems if p.get('code') != code]
                    return invoke
                stack.enter_context(patch.object(obligations, 'issues', filter_gate(
                    obligations.issues, 'pending_internal_obligation', 'pool_gate')))
                original = bindings.closure_issues
                for owner in (bindings, contracts):
                    stack.enter_context(patch.object(owner, 'closure_issues', filter_gate(
                        original, 'open_output_binding', 'binding_gate')))
            with super().installed():
                yield self
