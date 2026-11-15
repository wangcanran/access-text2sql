"""One compiler boundary for graph, obligations and question ownership.

Reuses frozen ID allocation/parking, not the v5 sequence of inference hooks.
"""
from copy import deepcopy

from clarification_contract_v5.bindings import (normalize_plan_bindings,
    remove_covered_summary_roots, route_unique_decision_dimensions,
    derive_requires_operand_claims, normalize_subject_edges, promote_claims,
    complete_open_question_claims, validate_claims)
from clarification_deferred_deletion.baseline import analysis, light
from clarification_deferred_deletion.graph import canonical_plan
from clarification_deferred_deletion.method import park
from clarification_deferred_v3_2.structure import check_structure
from .compiler import compile_subject_lineage, validate_subject_edges
from .prompts import PLAN, BINDINGS
from .pool_transitions import (compile_inherited_claims, compile_plan_dependencies,
    resolve_permission_probes)
from . import obligations as pool
from .question_registry import register_questions
from .state_sync import reconcile_state
from .ownership import compile_plan_references


def none_eligible(plan, claims, event):
    asked = set(event['asked_ids'])
    result = []
    for n in plan['nodes']:
        if n['parent'] != event['target'] or not set(n['decision_ids']) & asked:
            continue
        # A single declared contribution can have field/value/operation facets.
        # Losing its field invalidates its value too. Several independent
        # contribution IDs still require an explicit finer split.
        ids = {oid for c in claims[n['id']] for oid in pool._refs(c)}
        if set(n['decision_ids']) - asked and len(ids) != 1:
            raise ValueError('Split independent contribution obligations before NONE pruning')
        result.append(n['id'])
    if not result:
        raise ValueError('NONE must identify its asked contribution leaf')
    return result


def decompose(method, call, session, context, none_event=None):
    original_ledger = ((session.get('view') or {}).get('progressive') or {}).get('decision_ledger', [])
    before = resolve_permission_probes(pool.initialize(session))
    payload = method.plan_payload(before, context)
    payload['none_event'] = none_event
    accepted = {}
    pending = light.pending(before['view'])
    # Permission probes are internal and are removed from the user-facing
    # ledger below, but historical/model plans may still echo their IDs. They
    # remain valid references until that deterministic projection step.
    reference_pending = list(pending) + [d for d in original_ledger
                                         if d.get('dimension') == 'permission'
                                         and d.get('decision_id') not in {x['decision_id'] for x in pending}]
    by_id = {d['decision_id']: d for d in pending}
    parent_claims = {p: before['binding_claims'].get(p, []) for p in before['view']['items']}
    probes = {d['decision_id'] for d in before.get('internal_decision_audit', [])
              if d.get('kind') == 'permission_projection'}

    def check(compiled):
        # Plan references are compiled before semantic normalization.  This
        # keeps parent/decision/edge ownership independent from model wording.
        raw = compile_plan_references(compiled, before, reference_pending)
        normalize_plan_bindings(raw['nodes'], context)
        for n in raw['nodes']:
            if isinstance(n.get('decision_ids'), list):
                n['decision_ids'] = [d for d in n['decision_ids'] if d not in probes]
            if probes and isinstance(n.get('questions'), dict):
                n['questions'].pop('permission', None)
            compile_inherited_claims(n, n.get('parent'), before)
            pool.validate_role_claims(n, n['role'])
        raw = remove_covered_summary_roots(raw, set(before['view']['items']))
        raw = method._settled_plan(raw, before)
        route_unique_decision_dimensions(raw['nodes'], pending)
        # Dependency propagation consumes typed, normalized states. Running it
        # against optional raw model annotations misses bound operand providers.
        for n in raw['nodes']:
            if n.get('binding_claims'):
                n['binding_claims'] = validate_claims(n['binding_claims'], n['role'], context, n['kind'])
        for n in raw['nodes']:
            if not n.get('binding_claims'):
                complete_open_question_claims(n, [by_id[i] for i in n.get('decision_ids', []) if i in by_id])
        compile_plan_dependencies(raw['nodes'], before,
                                  raw.get('retired_dependencies', {}))
        derive_requires_operand_claims(raw['nodes'], raw['edges'])
        compile_subject_lineage(raw['nodes'], parent_claims)
        raw['edges'] = normalize_subject_edges(raw['nodes'], raw['edges'])
        validate_subject_edges(raw['nodes'], raw['edges'], parent_claims)
        local = {n['id']: promote_claims(n, n['role'], context, 'columns', n['kind'])
                 for n in raw['nodes']}
        plan = canonical_plan(raw, before['view'], context, pending)
        hypothetical = deepcopy(before)
        hypothetical['binding_claims'].update(local)
        replacements = {p: [n['id'] for n in plan['nodes'] if n['parent'] == p] for p in parent_claims}
        retired_columns = set(plan.get('retired_dependencies', {}))
        retired_by_parent = {
            p: sorted(retired_columns & set((before['view']['items'].get(p) or {})
                                            .get('required_columns', []) or []))
            for p in replacements}
        prepared = pool.transfer(before, hypothetical, replacements,
            retired=retired_by_parent, project=False)
        eligible = none_eligible(plan, local, none_event) if none_event else []
        accepted.clear()
        accepted.update(plan=deepcopy(plan), claims=prepared['binding_claims'],
                        pool=prepared['obligation_pool'], eligible=eligible)
        return plan

    stage = 'contract_none_decomposition' if none_event else 'contract_permission_decomposition'
    try:
        # UsefulResidualMethod's installed adapter compiles semantic_class /
        # affects to roles / typed edges before invoking this single validator.
        method._accepted_declarations = None
        plan = analysis.validated_call(call, stage, PLAN + BINDINGS, payload, check)
    except (ValueError, TypeError, KeyError) as exc:
        failed = deepcopy(before)
        failed['deferred_incomplete'] = True
        failed['contract_unresolved_reason'] = str(exc)
        return failed
    seeds = None
    if none_event:
        # Every eligible leaf carries an actually asked ID. Code locates these
        # by identity; a second model cannot broaden the NONE target.
        seeds = {i: dict(rule=none_event['trigger'], reason='NONE rejects this declared contribution',
                        asked_ids=list(none_event['asked_ids'])) for i in accepted['eligible']}
    try:
        result = park(before, plan, context, seeds)
        result['binding_claims'] = deepcopy(before['binding_claims'])
        batch = result['deferred_state']['batches'][-1]
        for alias, owner in batch['aliases'].items():
            result['binding_claims'][owner] = deepcopy(accepted['claims'][alias])
        result['obligation_pool'] = accepted['pool']
        if method._accepted_declarations:
            batch['contribution_declarations'] = deepcopy(method._accepted_declarations)
        batch['pruning_policy'] = method.VERSION
        batch['question_contributions'] = [dict(alias=n['id'], decision_ids=n['decision_ids'],
            obligation_ids=sorted({oid for c in accepted['claims'][n['id']] for oid in pool._refs(c)}))
            for n in plan['nodes']]
        # IDs and owners are stable only after park() compiles the aliases.
        # Publish and validate the queue exactly once at that boundary.
        result = register_questions(result)
        return reconcile_state(check_structure(pool.refresh(result, 'decompose')),
                               event='decomposition_publish')
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        failed = deepcopy(before)
        failed['deferred_incomplete'] = True
        failed['contract_unresolved_reason'] = 'Compiled decomposition publication failed: ' + str(exc)
        return failed
