"""Atomic graph/pool commit with a bounded, evidence-based semantic review."""
from copy import deepcopy

from clarification_contract_v5.method import ContractMethod
from clarification_deferred_deletion.baseline import analysis, light
from . import obligations as pool
from .pool_transitions import deletion_input

REVIEW = '''Review ONLY semantic correctness of this permission-reduced request.
Return JSON exactly {"accepted":true,"issues":[]} or
{"accepted":false,"issues":[{"code":"unsupported_dependency|unrepresented_intent|answer_conflict|residual_scope_change",
"node_ids":["existing node ID"],"evidence_id":"e1","quote":"exact substring of evidence",
"reason":"specific semantic mismatch"}]}.
Code computes permissions and graph closure; membership and lifecycle are not
model decisions. permission_facts and deletion_transaction are authoritative.
Do not recompute authorization, demand an answer on a blocked leaf or propose
alternative implementations. Losing a necessary cohort/subject must remove its
dependent outputs; never remove that restriction to keep an output alive.
A NONE or empty authorized candidate domain blocks the rejected contribution
and its necessary dependents. Other questions about that same contribution do
not require user answers after it is blocked. A defining subject/identity locator
with no surviving output consumer is not an independent requested output.
Any canonical leaf listed in deletion_transaction.drop_ids with a code-produced
block_proof is an authorized permission reduction. Do not report that leaf as
unrepresented_intent merely because it is absent from candidate_text; its
absence is the deletion being reviewed. Check instead that the drop proof is
consistent with the leaf's denied dependency and that surviving outputs do not
depend on it. Only independent requested outputs that were not code-blocked may
be reported as disappearing intent.
For candidate_action=refuse and zero remaining objectives, empty candidate_text
is required and valid. Do not reject only because the result is refusal.
Still reject independent requested outputs hidden in blocked compound nodes,
unsupported semantic requires edges, missing intended outputs from the canonical
inventory, changed surviving scopes, or invented/ignored actual answers.
Partial residual delivery is valid: if one independently requested output is
code-blocked, do not reject another surviving output merely because the original
compound sentence also mentioned the blocked output. Judge the surviving node's
own dependencies and scope. Only reject when the surviving node itself is
broadened, loses a necessary dependency, or claims to provide the blocked result.
Every rejection must cite exact provided intent/answer evidence. Schema presence
is not evidence of access. answer_conflict/residual_scope_change must identify
surviving nodes. For unrepresented_intent, node_ids may be empty. Return JSON only.
'''
CODES = {'unsupported_dependency', 'unrepresented_intent', 'answer_conflict', 'residual_scope_change'}


def validate_review(raw, payload):
    if (not isinstance(raw, dict) or set(raw) != {'accepted', 'issues'}
            or type(raw['accepted']) is not bool or not isinstance(raw['issues'], list)
            or raw['accepted'] != (not raw['issues'])):
        raise ValueError('Review must consistently accept or cite semantic issues')
    known = {n['id'] for n in payload['canonical_nodes']}
    active = {n['id'] for n in payload['surviving_nodes']}
    evidence = payload['evidence_catalog']
    blocked = set(payload.get('deletion_transaction', {}).get('drop_ids', []))
    proofs = payload.get('deletion_transaction', {}).get('block_proofs', [])
    proven = {alias for batch in proofs if isinstance(batch, dict)
              for alias in (batch.get('proofs') or {}).keys()}
    filtered = []
    for issue in raw['issues']:
        if (not isinstance(issue, dict)
                or set(issue) != {'code', 'node_ids', 'evidence_id', 'quote', 'reason'}
                or issue['code'] not in CODES or not isinstance(issue['node_ids'], list)
                or any(not isinstance(i, str) for i in issue['node_ids'])
                or len(set(issue['node_ids'])) != len(issue['node_ids'])
                or not set(issue['node_ids']) <= known
                or not isinstance(issue['reason'], str) or not issue['reason'].strip()
                or not isinstance(issue['evidence_id'], str) or issue['evidence_id'] not in evidence
                or not isinstance(issue['quote'], str) or not issue['quote'].strip()
                or issue['quote'] not in evidence[issue['evidence_id']]):
            raise ValueError('Semantic rejection requires known nodes and exact intent/answer evidence')
        # Permission deletion is code-owned. A reviewer cannot veto a valid
        # partial delivery merely because a blocked independent output is no
        # longer present in candidate_text. Keep issues that involve any
        # surviving/unproven node; discard only fully proven blocked leaves.
        if (issue['code'] == 'unrepresented_intent' and issue['node_ids']
                and set(issue['node_ids']) <= blocked and set(issue['node_ids']) <= proven):
            continue
        if issue['code'] != 'unrepresented_intent' and not issue['node_ids']:
            raise ValueError('Semantic issue must identify affected nodes')
        if issue['code'] in ('answer_conflict', 'residual_scope_change') and not set(issue['node_ids']) <= active:
            raise ValueError('Surviving-answer/scope issues cannot target code-blocked nodes')
        filtered.append(issue)
    result = deepcopy(raw)
    result['issues'] = filtered
    result['accepted'] = not filtered
    return result


def commit(method, call, session, context):
    # A deferred state is only a parked projection. It becomes a deletion
    # transaction after an explicit NONE/unanswerable or permission event.
    from .method import has_removal_authorization
    if not has_removal_authorization(session):
        blocked = deepcopy(session)
        blocked['deferred_incomplete'] = True
        blocked['contract_unresolved_reason'] = (
            'Deferred deletion has no user or permission authorization')
        return blocked
    if light.pending(session['view']) or any(n['missing'] for n in session['view']['items'].values()):
        raise ValueError('Cannot commit before eligible questions close')
    new = deepcopy(session)
    state = new['deferred_state']
    new['removal_no_query'] = not any(n['role'] == 'output' for n in new['view']['items'].values())
    state.update(committed=True, commit_turn=len(new.get('turns', [])))
    # Publish both authorities BEFORE rendering can dispatch to next_step.
    new = pool.refresh(new, 'commit_deletion')
    state = new['deferred_state']
    candidate = ContractMethod.render_session(method, call, new)
    if candidate['action'] not in ('deliver', 'refuse'):
        new['deferred_state']['committed'] = False
        new = pool.refresh(new, 'rollback_deletion')
        new['deferred_final'] = candidate
        return new
    evidence = {'e1': new['original_request']}
    for turn in new.get('turns', []):
        for value in [turn.get('answer', {}).get('text', '')] + [o.get('text', '') for o in turn.get('selected_options', [])]:
            if value:
                evidence['e' + str(len(evidence) + 1)] = value
    payload = deletion_input(dict(original_request=new['original_request'],
        historical_batches=state['batches'],
        canonical_nodes=[light.node_payload(n) for n in state['canonical_nodes'].values()],
        surviving_nodes=[light.node_payload(n) for n in new['view']['items'].values()],
        surviving_edges=new['view']['relations']['edges'], canonical_edges=state['canonical_typed_edges'],
        current_surviving_decisions=light.pending(new['view']), current_surviving_missing={},
        actual_answers=new.get('turns', []), candidate_text=candidate.get('text', ''),
        allowed_columns=new['allowed'], evidence_catalog=evidence), new)
    # The legacy audit adapter computes permission_facts. Forward those facts,
    # not another large membership list for the reviewer to reinterpret.
    def invoke(stage, prompt, supplied):
        supplied = deepcopy(supplied)
        active = supplied['input'] if stage.endswith('_contract_retry') else supplied
        active.pop('allowed_columns', None)
        return call(stage, REVIEW, supplied)
    try:
        review = analysis.validated_call(invoke, 'deferred_commit_review', REVIEW, payload,
                                         lambda raw: validate_review(raw, payload))
    except (ValueError, KeyError, TypeError) as exc:
        review = dict(accepted=False, issues=[dict(code='review_contract', reason=str(exc))])
    if not review['accepted']:
        new['deferred_state']['committed'] = False
        new = pool.refresh(new, 'rollback_deletion')
        new['deferred_review_rejection'] = review
        new['deferred_final'] = dict(action='unresolved', reason='deferred_semantic_review_rejected',
            text='', available_for_text2sql=False, issues=review['issues'])
        return new
    new.setdefault('reduction_audit', []).append(dict(method=method.VERSION,
        trigger='deferred_batch_commit', after_turn=len(new.get('turns', [])),
        previous_request=state['canonical_text'], request=new['current_request'],
        receipt=dict(drop_ids=sorted(state['blocked_nodes']), revise_ids=[], request=new['current_request']),
        semantic_review=review, blocked_at_turns=[b['turn'] for b in state['batches']],
        analysis_mode='one_atomic_graph_pool_commit'))
    new['deferred_final'] = candidate
    return new
