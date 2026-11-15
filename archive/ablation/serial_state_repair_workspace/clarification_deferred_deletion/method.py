"""Full canonical graph, question projection, and one deferred deletion commit."""
from copy import deepcopy

from .baseline import LightMainMethod, light, main, analysis, updates, module
from .graph import canonical_plan, closure, required_columns
from .prompts import PLAN

pending = module('experiments.decision_update.ledger').pending
QueryFrame = module('model').QueryFrame
signature = module('engine.simple_session').signature
has_query_objective = module('objective_contract').has_query_objective
VERSION = 'deferred_deletion_v2_local_none_split_current_review'

REVIEW = '''Review a FINAL permission-reduced natural-language query and its
deferred deletion transaction. Return exactly {"accepted":true,"issues":[]} or
{"accepted":false,"issues":["specific material error"]}. Do not rewrite SQL.
All requirements were retained in the canonical graph during clarification.
blocked leaves were excluded only from the question projection, not deleted.
Now clarification of eligible leaves has ended and code proposes one deletion.
Check coverage of ALL original business outputs/conditions against the recorded
split leaves. Do not accept independent outputs hidden inside a blocked compound
leaf. Necessary subject/locator/operand loss may remove dependent outputs; loss
of an optional condition must not erase unrelated outputs. Check each deletion
proof against the declared meaning, schema and original request. Do not propose
alternative implementations. Code owns permissions; you cannot grant access.
Check final surviving requirements and candidate_text against actual answers:
no missed outputs, invented thresholds/formulas, lost subject scope, resurrected
canceled content, or remaining ambiguity disguised as a complete request.
Real schema names in final Text2SQL input are allowed. They are forbidden in
user menus. A generic output container must not repeat removed predicates.
Unselected candidate fields are NOT confirmed mandatory dependencies. Unanswered
questions on correctly blocked/deleted leaves do NOT prevent final closure.
All questions on surviving leaves must be answered or explicitly unresolved.
New questions discovered during decomposition are registered by code, not
required to reuse unrelated old decision IDs. Necessary dependencies are
transitive; redundant direct edges are not required. Do not demand fully defining
formulas or field choices on leaves already necessarily blocked by an independent
subject denial; review whether their removal is justified instead.
Judge observable final semantics and correct removal scope. Return JSON only.
Historical decomposition questions are NOT current unresolved questions. The
current_surviving_decisions and current_surviving_missing fields are authoritative
for what remains unanswered now. Historical span wording does not reopen a
question that actual answers resolved. Independently check whether those answers
support the final text; reject genuine unsupported completion, not stale history.
'''

WITHDRAW = '''Locate the exact semantic leaf contribution rejected by the current
NONE/empty-menu event. Return exactly {"blocked_aliases":["p1"],"reason":"..."}.
Use only aliases in the proposed split leaves. The question/decision concern
ONE local purpose, not all requirements in its old compound parent. Preserve
independent remaining_requirements, returned results and associated details.
Do not cancel the whole parent merely because it received NONE. If the rejected
purpose is a modifier, retain its output. Do not invent replacement intent.
Code later queues these leaves for deferred removal and computes dependent
closure; you only locate the current event's rejected contribution.
'''


def split_graph(session, plan, context):
    base = session['view']
    columns = required_columns(plan)
    patch = {'updates': {i: [] for i in base['items']},
             'edges': [[a, b, 'context' if k == 'requires' else k] for a, b, k in plan['edges']]}
    for n in plan['nodes']:
        tables = sorted({c.rsplit('.', 1)[0] for c in columns[n['id']] | set(n['candidates'])})
        patch['updates'][n['parent']].append({
            'id': n['id'], 'text': n['text'], 'role': n['role'], 'kind': n['kind'],
            'columns': sorted(columns[n['id']]), 'tables': tables or base['items'][n['parent']]['scope_tables'],
            'questions': n['questions'], 'candidates': n['candidates'], 'pending': n['decision_ids']})
    target = next(iter(base['items']))
    event = {'kind': 'removal', 'target': target, 'dimension': 'permission',
             'question': 'Internal decomposition, no deletion authorized',
             'decision': {'role': base['items'][target]['role']}}
    split = light.transaction(session, base, event, patch, context, editable_ids=set(base['items']))
    info = split['view']['progressive']
    counts = {}
    for d in info['decision_ledger']:
        counts[d['decision_id']] = counts.get(d['decision_id'], 0) + 1
    for d in info['decision_ledger']:
        if counts[d['decision_id']] > 1:
            d['ancestor_decision_id'] = d['decision_id']
            d['decision_id'] += '/' + d['owner_id']
    aliases = {}
    for parent, children in split['transaction_audit'][-1]['replacements'].items():
        aliases.update({n['id']: iid for n, iid in zip(
            [n for n in plan['nodes'] if n['parent'] == parent], children)})
    info['event_kind'] = 'deferred_decomposition'
    split['transaction_audit'][-1]['event']['kind'] = 'decomposition'
    info['question_policy'] = 'answer_delta'
    split['view']['segmentation']['inventory']['progressive'] = deepcopy(info)
    return split, aliases


def reframe(session, context):
    """Rebuild derived routing metadata from this projection only."""
    view = session['view']
    text = light.projection(view['items'], view['relations']['edges'])
    session['current_request'] = text
    view['frame'] = QueryFrame(text or 'No eligible questions.',
        tuple({'item_id': i, 'span': n['span']} for i, n in view['items'].items() if n['role'] == 'output'),
        tuple({'item_id': i, 'span': n['span']} for i, n in view['items'].items() if n['role'] == 'modifier')).to_json()
    view['progressive']['question_policy'] = 'answer_delta'
    view['segmentation']['inventory']['progressive'] = deepcopy(view['progressive'])
    view['questions'], view['deferred'], view['candidate_audit'] = [], [], []
    # The inventory and node identities changed; old cross-node answer groups
    # are no longer valid routing evidence.
    view['shared_decisions'] = []
    session['permission_issues'] = {}
    session['pending'], session['menu_frontiers'], session['menu_blockers'] = None, {}, {}
    for key in ('lineage_pending', 'lineage_review_signature', 'lineage_resync_signature'):
        session.pop(key, None)
    session['removal_no_query'] = False
    session = updates.finish_view(session, context)
    main.check_active_state(session)
    return session


def park(session, plan, context, seeds=None):
    """Save every leaf in canonical state; restrict only the question view."""
    split, aliases = split_graph(session, plan, context)
    blocked = closure(plan, split['allowed'], seeds)
    ids = {aliases[a] for a in blocked}
    state = deepcopy(split.get('deferred_state', {}))
    state.setdefault('blocked_nodes', {}).update({i: deepcopy(n) for i, n in split['view']['items'].items() if i in ids})
    state.setdefault('blocked_decisions', []).extend(
        deepcopy(d) for d in pending(split['view']) if d['owner_id'] in ids)
    state.setdefault('blocked_edges', []).extend(deepcopy(e) for e in split['view']['relations']['edges']
        if e['source_id'] in ids or e['target_id'] in ids)
    typed_edges = [{'source_id': aliases[a], 'target_id': aliases[b], 'kind': kind}
                   for a, b, kind in plan['edges']]
    state.setdefault('batches', []).append({'plan': deepcopy(plan), 'aliases': aliases,
        'typed_edges': typed_edges,
        'block_proofs': blocked, 'source_inventory': session['view']['inventory_id'],
        'previous_request': session['current_request'], 'turn': len(session['turns'])})
    state.setdefault('permission_evidence', []).append(deepcopy(main.evidence_sources(session)))
    state['committed'] = False
    split['deferred_state'] = state
    split['view']['items'] = {i: n for i, n in split['view']['items'].items() if i not in ids}
    split['view']['relations']['edges'] = [e for e in split['view']['relations']['edges']
        if e['source_id'] not in ids and e['target_id'] not in ids]
    split['view']['progressive']['decision_ledger'] = [d for d in pending(split['view']) if d['owner_id'] not in ids]
    split['view']['progressive']['editable_ids'] = sorted(split['view']['items'])
    split['view']['inventory_id'] = signature(split['view']['inventory_id'], 'question_projection', sorted(ids))
    split = reframe(split, context)
    return sync_canonical(split)


def sync_canonical(session):
    """Accessible answers replace accessible leaves; blocked leaves stay intact."""
    state = session.get('deferred_state')
    if not state or state['committed']:
        return session
    state['canonical_nodes'] = {**deepcopy(state['blocked_nodes']), **deepcopy(session['view']['items'])}
    state['canonical_decisions'] = deepcopy(state['blocked_decisions']) + pending(session['view'])
    # Answer transactions allocate descendant IDs. Remap only attachments; do
    # not replay old compound text or copy canceled requirements into answers.
    current = set(state['canonical_nodes'])
    retired = session['view']['progressive'].get('retired', {})
    def descendants(iid, seen=None):
        if iid in current:
            return [iid]
        seen = set() if seen is None else seen
        if iid in seen:
            return []
        seen.add(iid)
        return [c for child in retired.get(iid, {}).get('children', []) for c in descendants(child, seen.copy())]
    mapped = []
    for e in state['blocked_edges']:
        for a in descendants(e['source_id']):
            for b in descendants(e['target_id']):
                if a != b:
                    mapped.append({'source_id': a, 'target_id': b, 'kind': e['kind']})
    typed = []
    for batch in state.get('batches', []):
        for edge in batch.get('typed_edges', []):
            for a in descendants(edge['source_id']):
                for b in descendants(edge['target_id']):
                    if a != b:
                        typed.append({'source_id': a, 'target_id': b, 'kind': edge['kind']})
    # Prefer typed dependency edges over the compatibility context edge emitted
    # by the frozen lightweight transaction for the same endpoints.
    typed = list({(e['source_id'], e['target_id'], e['kind']): e for e in typed}.values())
    typed_pairs = {(e['source_id'], e['target_id']) for e in typed}
    compatibility = mapped + deepcopy(session['view']['relations']['edges'])
    merged = list(typed)
    seen = {(e['source_id'], e['target_id'], e['kind']) for e in merged}
    for edge in compatibility:
        pair = (edge['source_id'], edge['target_id'])
        key = (edge['source_id'], edge['target_id'], edge['kind'])
        if pair in typed_pairs and edge['kind'] == 'context':
            continue
        if key not in seen:
            merged.append(edge)
            seen.add(key)
    state['canonical_typed_edges'] = deepcopy(typed)
    state['canonical_edges'] = merged
    state['canonical_text'] = light.projection(state['canonical_nodes'], state['canonical_edges'])
    if set(state['blocked_nodes']) & set(session['view']['items']):
        raise ValueError('Blocked node entered eligible question projection')
    if any(d['owner_id'] not in state['canonical_nodes'] for d in state['canonical_decisions']):
        raise ValueError('Canonical pending decision lost its owner')
    return session


def check_review(raw):
    if (not isinstance(raw, dict) or set(raw) != {'accepted', 'issues'}
            or type(raw['accepted']) is not bool or not isinstance(raw['issues'], list)
            or any(not isinstance(x, str) or not x.strip() for x in raw['issues'])
            or raw['accepted'] != (not raw['issues'])):
        raise ValueError('Expected accepted boolean and consistent issues array')
    return raw


class DeferredDeletion(LightMainMethod):
    def prepare_menu(self, call, session, plan, context, max_options=5):
        updated, public = super().prepare_menu(call, session, plan, context, max_options)
        if public is None and updated.get('deferred_state') and not updated.get('deferred_incomplete'):
            updated = self.analyze_session(call, updated, context)
        return updated, public

    def _remove(self, call, session, basis, q, trigger, context):
        """A NONE/empty menu queues a withdrawal; it never commits a deletion."""
        decision = q.get('scoped_permission_decision', {})
        if decision.get('remaining_requirements'):
            result = deepcopy(basis)
            for key in ('turns', 'public_interactions'):
                result[key] = deepcopy(session.get(key, []))
            base = result['view']
            payload = self.plan_payload(result, context)
            plan = analysis.validated_call(call, 'deferred_none_decomposition', PLAN, payload,
                lambda raw: canonical_plan(raw, base, context, pending(base)))
            def check_target(raw):
                if (not isinstance(raw, dict) or set(raw) != {'blocked_aliases', 'reason'}
                        or not isinstance(raw['blocked_aliases'], list) or not raw['blocked_aliases']
                        or len(set(raw['blocked_aliases'])) != len(raw['blocked_aliases'])
                        or not isinstance(raw['reason'], str) or not raw['reason'].strip()):
                    raise ValueError('Expected blocked_aliases and reason')
                allowed = {n['id'] for n in plan['nodes'] if n['parent'] == q['item']['item_id']}
                if not set(raw['blocked_aliases']) <= allowed:
                    raise ValueError('Withdrawal must stay in questioned parent descendants')
                return raw
            focus = analysis.validated_call(call, 'deferred_none_focus', WITHDRAW,
                {'question': q['focus'], 'decision': decision, 'trigger': trigger, 'plan': plan}, check_target)
            seeds = {i: {'rule': trigger, 'reason': focus['reason'], 'decision': decision}
                     for i in focus['blocked_aliases']}
            return park(result, plan, context, seeds)
        result = deepcopy(basis)
        # Preserve the actual NONE answer and public interaction records.
        for key in ('turns', 'public_interactions'):
            result[key] = deepcopy(session.get(key, []))
        target = q['item']['item_id']
        view = result['view']
        state = deepcopy(result.get('deferred_state', {}))
        state.setdefault('blocked_nodes', {})[target] = deepcopy(view['items'][target])
        state.setdefault('blocked_decisions', []).extend(deepcopy(d) for d in pending(view) if d['owner_id'] == target)
        state.setdefault('blocked_edges', []).extend(deepcopy(e) for e in view['relations']['edges']
            if target in (e['source_id'], e['target_id']))
        state.setdefault('batches', []).append({'trigger': trigger, 'turn': len(result['turns']),
            'target': target, 'decision': deepcopy(decision),
            'block_proofs': {target: {'rule': trigger}}, 'previous_request': result['current_request']})
        state.setdefault('permission_evidence', []).append(deepcopy(main.evidence_sources(basis)))
        state['committed'] = False
        result['deferred_state'] = state
        view['items'].pop(target)
        view['relations']['edges'] = [e for e in view['relations']['edges']
            if target not in (e['source_id'], e['target_id'])]
        view['progressive']['decision_ledger'] = [d for d in pending(view) if d['owner_id'] != target]
        view['inventory_id'] = signature(view['inventory_id'], 'deferred_withdrawal', target)
        return sync_canonical(reframe(result, context))

    def plan_payload(self, session, context):
        base = session['view']
        return {'current_request': session['current_request'],
            'parents': [{**light.node_payload(n), 'span': n['span']} for n in base['items'].values()],
            'previous_edges': [[e[k] for k in ('source_id', 'target_id', 'kind')] for e in base['relations']['edges']],
            'pending_decisions': pending(base), 'schema': light.schema_payload(context),
            'actual_answers': session['turns']}

    def analyze_session(self, call, session, context, reconstruct=None):
        analyzed = super().analyze_session(call, session, context, reconstruct)
        if not analyzed.get('view'):
            return analyzed
        if main.evidence_sources(analyzed) and not analyzed.get('removal_no_query'):
            base = analyzed['view']
            payload = self.plan_payload(analyzed, context)
            plan = analysis.validated_call(call, 'deferred_decomposition', PLAN, payload,
                lambda raw: canonical_plan(raw, base, context, pending(base)))
            analyzed = park(analyzed, plan, context)
        analyzed = sync_canonical(analyzed)
        state = analyzed.get('deferred_state')
        if state and not state['committed']:
            route = super().next_step(analyzed)
            if route['action'] not in ('ask', 'menu', 'analyze'):
                # Only actual closure qualifies. Deferral, submit, or an unrouted
                # gap must never be relabeled as complete to save model calls.
                if pending(analyzed['view']) or any(n['missing'] for n in analyzed['view']['items'].values()):
                    analyzed['deferred_incomplete'] = True
                    return analyzed
                return self.commit_deferred(call, analyzed, context)
        return analyzed

    def commit_deferred(self, call, session, context):
        new = deepcopy(session)
        state = new['deferred_state']
        if pending(new['view']) or any(n['missing'] for n in new['view']['items'].values()):
            raise ValueError('Cannot commit before eligible clarification closes')
        # No model reconstructs the graph here: use the exact latest leaves.
        new['removal_no_query'] = not has_query_objective(new['view']['items'].values())
        state['committed'] = True
        state['commit_turn'] = len(new['turns'])
        candidate = super().render_session(call, new)
        payload = {'original_request': new['original_request'], 'historical_batches': state['batches'],
            'canonical_nodes': [light.node_payload(n) for n in state['canonical_nodes'].values()],
            'blocked_decisions': state['blocked_decisions'],
            'surviving_nodes': [light.node_payload(n) for n in new['view']['items'].values()],
            'surviving_edges': new['view']['relations']['edges'],
            'current_surviving_decisions': pending(new['view']),
            'current_surviving_missing': {i: n['question_focus'] for i, n in new['view']['items'].items()},
            'candidate_text': candidate.get('text', ''), 'actual_answers': new['turns'],
            'allowed_columns': new['allowed'], 'schema': light.schema_payload(context)}
        review = analysis.validated_call(call, 'deferred_commit_review', REVIEW, payload, check_review)
        if not review['accepted']:
            new['deferred_review_rejection'] = review
            new['deferred_final'] = {'action': 'unresolved', 'reason': 'deferred_semantic_review_rejected',
                'text': '', 'available_for_text2sql': False, 'issues': review['issues']}
            state['committed'] = False
            return new
        new.setdefault('reduction_audit', []).append({
            'method': VERSION, 'trigger': 'deferred_batch_commit', 'after_turn': len(new['turns']),
            'previous_request': state['canonical_text'], 'request': new['current_request'],
            'receipt': {'drop_ids': sorted(state['blocked_nodes']), 'revise_ids': [],
                        'request': new['current_request']}, 'semantic_review': review,
            'blocked_at_turns': [b['turn'] for b in state['batches']],
            'analysis_mode': 'one_commit_after_eligible_clarification'})
        new['deferred_final'] = candidate
        return new

    def next_step(self, session):
        if session.get('deferred_final'):
            final = session['deferred_final']
            return {'action': final['action'], 'reason': final.get('reason', ''), 'turn': len(session['turns'])}
        if session.get('deferred_incomplete'):
            return {'action': 'unresolved', 'reason': 'eligible_clarification_incomplete', 'turn': len(session['turns'])}
        return super().next_step(session)

    def render_session(self, call, session):
        if session.get('deferred_final'):
            return deepcopy(session['deferred_final'])
        if session.get('deferred_state') and not session['deferred_state']['committed']:
            return {'action': 'unresolved', 'reason': 'deferred_deletion_not_committed',
                    'text': '', 'available_for_text2sql': False}
        return super().render_session(call, session)
