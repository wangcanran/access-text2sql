"""Shared semantic contracts at planning, answer, pruning and delivery boundaries."""
from contextlib import contextmanager
from copy import deepcopy
import json
import re
from unittest.mock import patch

from clarification_deferred_deletion.baseline import LightMainMethod, analysis, light, main, module
from clarification_deferred_deletion.method import park, sync_canonical, reframe
from clarification_deferred_deletion.graph import canonical_plan
from clarification_deferred_deletion.prompts import PLAN
from clarification_deferred_v3_2.method import TypedDeferredDeletion
from clarification_deferred_v3_2.prompts import DECISION_CONTRACT, DECISION_SCHEMA, INITIAL_QUESTION_POLICY, ANSWER_UPDATE
from clarification_deferred_v3_2.structure import check_structure
from .bindings import (promote_claims, closure_issues, complete_open_question_claims,
                       derive_requires_operand_claims, normalize_subject_edges,
                       remove_covered_summary_roots, validate_subject_edges,
                       normalize_plan_bindings)
from .bindings import compile_subject_lineage
from .bindings import route_unique_decision_dimensions
from .contracts import normalize_choices, normalize_questions
from .prompts import ANSWER_TYPES, MAPPING_CONTRACT, UPDATE_BINDINGS, DECOMPOSE_BINDINGS, WITHDRAW

VERSION = 'contract_v5_25_subject_obligation_compiler'
pending = module('experiments.decision_update.ledger').pending


def validate_requester_literal(raw, payload):
    """Keep an explicitly named entity when the current question asks for it."""
    if not isinstance(raw, dict) or raw.get('action') != 'answer':
        return raw
    question = payload.get('question', {}).get('question', '')
    original = payload.get('private_original', '')
    if not isinstance(question, str) or not isinstance(original, str):
        return raw
    if not re.search(r'\b(?:which|what)\s+(?:particular|specific|named)\b', question, re.I):
        return raw
    literals = re.findall(r'["“]([^"”]+)["”]', original)
    if len(literals) == 1 and literals[0].casefold() not in str(raw.get('answer', '')).casefold():
        raise ValueError('Answer the named entity in private_original; do not substitute a related category')
    return raw


class ContractMethod(TypedDeferredDeletion):
    @contextmanager
    def installed(self):
        original = analysis.validated_call

        def validated(call, stage, prompt, payload, validator):
            if stage == 'session_user_gold':
                prompt += '\nWhen asked which particular named entity, preserve its exact name from private_original.\n'
                return original(call, stage, prompt, payload,
                                lambda raw: validator(validate_requester_literal(raw, payload)))
            if stage == 'progressive_coarse':
                return original(call, stage, prompt + INITIAL_QUESTION_POLICY + ANSWER_TYPES, payload,
                                lambda raw: validator(normalize_choices(raw)))
            if stage == 'progressive_analysis':
                def mapping(raw):
                    raw = deepcopy(raw)
                    if not isinstance(raw, dict) or not isinstance(raw.get('items'), list):
                        raise ValueError('Expected items array')
                    claims = {}
                    for row in raw.get('items', []):
                        normalize_questions(row)
                        claims[row['item_id']] = promote_claims(row, row['role'], self._context,
                                                                'required_columns', row.get('kind'))
                    result = validator(raw)
                    self._initial_claims = claims
                    return result
                return original(call, stage, prompt + INITIAL_QUESTION_POLICY + ANSWER_TYPES + MAPPING_CONTRACT,
                                payload, mapping)
            if stage in ('field_decision', 'scope_decision', 'permission_decision'):
                return original(call, stage, prompt, payload,
                                lambda raw: validator(self._resolve_candidates(raw)))
            return original(call, stage, prompt, payload, validator)

        with super().installed(), patch.object(analysis, 'validated_call', validated):
            yield self

    def decision_input(self, prompt, payload):
        _, payload = super().decision_input(prompt, payload)
        columns = [t + '.' + c for t, cs in payload['schema']['tables'].items() for c in cs]
        self._candidate_catalog = {f'c{i}': c for i, c in enumerate(columns, 1)}
        payload['candidate_catalog'] = self._candidate_catalog
        schema = deepcopy(DECISION_SCHEMA)
        schema['properties']['field_candidates']['items']['properties']['column'] = {
            'enum': list(self._candidate_catalog)}
        # Replace only the schema section of the one complete contract.
        start = DECISION_CONTRACT.index('{')
        _, length = json.JSONDecoder().raw_decode(DECISION_CONTRACT[start:])
        prompt = DECISION_CONTRACT[:start] + json.dumps(schema) + DECISION_CONTRACT[start + length:]
        prompt += '''\nCandidate columns must use candidate_catalog handles, never typed schema
names or invented columns. Code maps handles back to real fields and authorizes
before menu publication. required_columns and scope_choices still use real
schema names. A value question must be reclassified to value and asked openly;
never offer a field/locator or invented example value as the requested fact.
'''
        return prompt, payload

    def _resolve_candidates(self, raw):
        raw = deepcopy(raw)
        if not isinstance(raw, dict) or not isinstance(raw.get('field_candidates'), list):
            raise ValueError('Expected field_candidates array')
        for option in raw.get('field_candidates', []):
            ref = option.get('column')
            if ref not in self._candidate_catalog:
                raise ValueError('Unknown candidate handle; select only from candidate_catalog')
            option['column'] = self._candidate_catalog[ref]
        return raw

    def commit_transaction(self, session, base, event, raw, context):
        raw = light.normalize_transaction(raw)
        if not isinstance(raw, dict):
            raise ValueError('Expected transaction object')
        if not isinstance(raw.get('updates'), dict):
            raise ValueError('Expected updates object')
        local_claims = {}
        for parent, rows in raw.get('updates', {}).items():
            for child in rows:
                role = child.get('role', base['items'][parent]['role'])
                if base['items'][parent]['role'] == 'modifier':
                    role = 'modifier'
                kind = child.get('kind', base['items'][parent]['kind'])
                local_claims[child['id']] = promote_claims(child, role, context, 'columns', kind)
        updated = super().commit_transaction(session, base, event, raw, context)
        # The transaction changes the inventory. Shared-decision links are
        # derived from the previous inventory and must not route later answers.
        updated['view']['shared_decisions'] = []
        updated['binding_claims'] = deepcopy(session.get('binding_claims', {}))
        replacements = updated['transaction_audit'][-1]['replacements']
        for parent, children in replacements.items():
            for raw_child, iid in zip(raw['updates'][parent], children):
                updated['binding_claims'][iid] = local_claims[raw_child['id']]
        return updated

    def _transaction(self, call, session, base, event, context):
        def contract_call(stage, prompt, payload):
            payload = deepcopy(payload)
            active = payload['input'] if stage.endswith('_contract_retry') else payload
            active['binding_claims'] = {i: session.get('binding_claims', {}).get(i, [])
                                        for i in base['items']}
            return call(stage, ANSWER_UPDATE + UPDATE_BINDINGS, payload)
        return LightMainMethod._transaction(self, contract_call, session, base, event, context)

    def plan_payload(self, session, context):
        payload = super().plan_payload(session, context)
        payload['binding_claims'] = {i: session.get('binding_claims', {}).get(i, [])
                                     for i in session['view']['items']}
        payload['subject_obligations'] = {
            i: [dict(meaning=c['meaning'], columns=c['columns']) for c in claims
                if c.get('use') == 'subject']
            for i, claims in payload['binding_claims'].items()
            if any(c.get('use') == 'subject' for c in claims)}
        return payload

    def _decompose(self, call, session, context, none_event=None):
        payload = self.plan_payload(session, context)
        payload['none_event'] = none_event
        claims = {}

        def check(raw):
            if not isinstance(raw, dict) or not isinstance(raw.get('nodes'), list):
                raise ValueError('Expected decomposition object with nodes array')
            if any(not isinstance(node, dict) for node in raw['nodes']):
                raise ValueError('Every decomposition node must be an object')
            for node in raw['nodes']:
                for key, expected in (('binding_claims', list), ('columns', list),
                                      ('questions', dict), ('candidates', dict),
                                      ('decision_ids', list)):
                    if key in node and not isinstance(node[key], expected):
                        raise ValueError(f'Decomposition {key} must be {expected.__name__}')
            normalize_plan_bindings(raw.get('nodes', []), context)
            raw = remove_covered_summary_roots(raw, set(session['view']['items']))
            raw = self._settled_plan(raw, session)
            local = {}
            by_id = {d['decision_id']: d for d in pending(session['view'])}
            route_unique_decision_dimensions(raw.get('nodes', []), list(by_id.values()))
            edges = raw.get('edges', [])
            if not isinstance(edges, list):
                raise ValueError('Plan edges must be an array')
            # A directed requires edge already states that a node depends on
            # another node. Carry an explicitly bound operand claim across
            # that declared edge so duplicated model annotations are not a
            # second authority.
            derive_requires_operand_claims(raw.get('nodes', []), edges)
            compile_subject_lineage(raw.get('nodes', []),
                {parent: session.get('binding_claims', {}).get(parent, [])
                 for parent in session['view']['items']})
            raw['edges'] = normalize_subject_edges(raw.get('nodes', []), edges)
            validate_subject_edges(raw.get('nodes', []), raw['edges'],
                                   {parent: session.get('binding_claims', {}).get(parent, [])
                                    for parent in session['view']['items']})
            for node in raw.get('nodes', []):
                node_decisions = [by_id[i] for i in node.get('decision_ids', []) if i in by_id]
                complete_open_question_claims(node, node_decisions)
                local[node['id']] = promote_claims(node, node['role'], context, 'columns',
                                                    node.get('kind'))
            plan = canonical_plan(raw, session['view'], context, pending(session['view']))
            if none_event:
                asked = set(none_event['asked_ids'])
                owners = [n for n in plan['nodes'] if set(n['decision_ids']) & asked]
                if not owners or any(set(n['decision_ids']) - asked for n in owners):
                    raise ValueError('Split asked purpose from all other pending decisions before pruning')
            claims.clear()
            claims.update(local)
            return plan

        stage = 'contract_none_decomposition' if none_event else 'contract_permission_decomposition'
        try:
            plan = analysis.validated_call(call, stage, PLAN + DECOMPOSE_BINDINGS, payload, check)
        except (ValueError, TypeError, KeyError) as exc:
            session = deepcopy(session)
            session['deferred_incomplete'] = True
            session['contract_unresolved_reason'] = str(exc)
            return session
        seeds = None
        if none_event:
            asked = set(none_event['asked_ids'])
            eligible = [n['id'] for n in plan['nodes'] if n['parent'] == none_event['target']
                        and set(n['decision_ids']) & asked and not set(n['decision_ids']) - asked]
            def check_focus(raw):
                if (not isinstance(raw, dict) or set(raw) != {'blocked_aliases', 'reason'}
                        or not isinstance(raw['blocked_aliases'], list) or not raw['blocked_aliases']
                        or any(not isinstance(i, str) for i in raw['blocked_aliases'])
                        or len(raw['blocked_aliases']) != len(set(raw['blocked_aliases']))
                        or not set(raw['blocked_aliases']) <= set(eligible)
                        or not isinstance(raw['reason'], str) or not raw['reason'].strip()):
                    raise ValueError('Seed only the asked-purpose leaves, never other pending contributions')
                return raw
            focus = analysis.validated_call(call, 'contract_none_focus', WITHDRAW,
                dict(none_event=none_event, plan=plan, eligible_seed_aliases=eligible), check_focus)
            seeds = {i: dict(rule=none_event['trigger'], reason=focus['reason'])
                     for i in focus['blocked_aliases']}
        # Canonical plan validation and permission closure use promoted claims.
        updated = park(session, plan, context, seeds)
        updated['binding_claims'] = deepcopy(session.get('binding_claims', {}))
        aliases = updated['deferred_state']['batches'][-1]['aliases']
        for alias, iid in aliases.items():
            updated['binding_claims'][iid] = claims[alias]
        return check_structure(updated)

    def _remove(self, call, session, basis, q, trigger, context):
        result = deepcopy(basis)
        for key in ('turns', 'public_interactions'):
            result[key] = deepcopy(session.get(key, []))
        asked = q.get('decision_ids', [])
        known = {d['decision_id'] for d in pending(basis['view']) if d['owner_id'] == q['item']['item_id']}
        if not asked or not set(asked) <= known:
            raise ValueError('NONE pruning requires actual current asked decision identities')
        # Never let remaining_requirements=[] authorize dropping a compound owner.
        return self._decompose(call, result, context, dict(target=q['item']['item_id'],
            asked_ids=asked, question=q['focus'], trigger=trigger,
            decision=q.get('scoped_permission_decision', {})))

    def analyze_session(self, call, session, context, reconstruct=None):
        self._context = context
        initial = session.get('view') is None and not session.get('update_base')
        # Reuse frozen initial analysis/answer transaction, then attach contracts
        # BEFORE the deferred permission layer may project out any nodes.
        if session.get('view') is None:
            analyzed = LightMainMethod.analyze_session(self, call, session, context, reconstruct)
        else:
            analyzed = session
        if not analyzed.get('view'):
            return analyzed
        if initial:
            analyzed['binding_claims'] = deepcopy(self._initial_claims)
        if main.evidence_sources(analyzed) and not analyzed.get('removal_no_query'):
            analyzed = self._decompose(call, analyzed, context)
        analyzed = sync_canonical(analyzed)
        state = analyzed.get('deferred_state')
        if state and not state['committed']:
            route = LightMainMethod.next_step(self, analyzed)
            if route['action'] not in ('ask', 'menu', 'analyze'):
                if pending(analyzed['view']) or any(n['missing'] for n in analyzed['view']['items'].values()):
                    analyzed['deferred_incomplete'] = True
                elif not closure_issues(analyzed):
                    analyzed = self.commit_deferred(call, analyzed, context)
        return check_structure(analyzed)

    def next_step(self, session):
        if session.get('contract_unresolved_reason'):
            return dict(action='unresolved', reason='semantic_graph_unresolved',
                        detail=session['contract_unresolved_reason'],
                        turn=len(session.get('turns', [])))
        if (session.get('view') and not pending(session['view'])
                and any(n.get('missing') for n in session['view']['items'].values())):
            return dict(action='unresolved', reason='unresolved_node_gaps',
                        turn=len(session.get('turns', [])))
        plan = super().next_step(session)
        if plan['action'] == 'deliver' and session.get('view'):
            if pending(session['view']):
                return dict(action='unresolved', reason='pending_decisions_without_route',
                            turn=len(session['turns']))
            if any(n.get('missing') for n in session['view']['items'].values()):
                return dict(action='unresolved', reason='unresolved_node_gaps',
                            turn=len(session['turns']))
        # Do not let internal binding closure preempt routable user questions.
        # Open output claims are expected while a field/value decision is still
        # pending; closure is checked only after the public clarification queue
        # and node gaps are empty.
        if (plan['action'] in ('deliver', 'refuse') and session.get('view')
                and not pending(session['view'])
                and not any(n.get('missing') for n in session['view']['items'].values())
                and closure_issues(session)):
            return dict(action='unresolved', reason='binding_coverage_incomplete', turn=len(session['turns']))
        return plan

    def render_session(self, call, session):
        if session.get('contract_unresolved_reason'):
            return dict(action='unresolved', reason='semantic_graph_unresolved',
                        detail=session['contract_unresolved_reason'], text='',
                        available_for_text2sql=False)
        issues = closure_issues(session)
        if issues:
            return dict(action='unresolved', reason='binding_coverage_incomplete', issues=issues,
                        text='', available_for_text2sql=False)
        if session.get('view') and any(n.get('missing') for n in session['view']['items'].values()):
            return dict(action='unresolved', reason='unresolved_node_gaps',
                        text='', available_for_text2sql=False)
        return super().render_session(call, session)
