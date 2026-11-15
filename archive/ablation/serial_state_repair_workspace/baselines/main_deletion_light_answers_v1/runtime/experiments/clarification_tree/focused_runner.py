"""Focus-owned interaction loop. Legacy runner is retained only for regression."""
from copy import deepcopy
from ...engine.analysis import validated_call
from .state import VERSION, initial, frontier, graph, require, text, validate
from .planner import next_step
from .permissions import safe, dependencies, node_dependencies
from .transaction import cancel, DIMENSIONS
from .contribution import prepare, apply
from .runner import menu, choices, Budget
from .prompts import RENDER
from .focus_context import payload as focus_payload
from .local_field_menu import candidates as local_field_candidates
from .local_field_menu import domain as local_field_domain
from .answer_scope import review as review_answer_scope
from .semantic_review import prepare_reviewed
from .prompt_contracts import CONTENT_TYPES, RELATION_TYPES, FOCUS_PLAN_TYPES, ANSWER_TYPES

ISSUE_CONTRACT = '\nAllowed question dimensions: ' + ', '.join(DIMENSIONS) + '''.
boundary: independent result boundaries and decomposition.
relation: which subject or output a condition/reference applies to, including client association.
field: which data attributes are needed.
operation: formula, aggregation, ranking, or other computation semantics.
value: concrete parameters, dates, temporal boundaries, thresholds, and units.
Use these exact dimension names; time_scope is a computation property, not a question dimension.
'''

PLAN = '''You are a local clarification planner, not a SQL generator. Return one JSON object.
Before writing field arrays, decide each part's granularity:
* compound/undetermined: columns may contain ONLY entries already present in
  editable_leaf.columns; scope_columns may contain ONLY entries already present
  in editable_leaf.scope_columns. If those source arrays are empty, return [].
  This rule applies separately to focus AND EVERY remainder. A field's existence
  in schema, plausibility, or use by an atomic sibling does not establish it for
  a compound part. Preserve unbound subject/time requirements in meaning instead.
* atomic: schema-supported definite dependencies may be proposed locally;
  field alternatives remain code-owned menu candidates, not binding unions.
Example with empty parent bindings: a compound "prior borrowing summary" remainder
has columns=[] and scope_columns=[], even if loans are its likely data source.
Keep the specified-loan subject in its meaning; do not enumerate likely loan fields.
Locate ONE semantic contribution in editable_leaf before asking.
ONLY editable_issues may occur in issue_owners or question.issue_id.
issue_owners keys must equal allowed_issue_owner_ids exactly: if [], return {}.
read_only_context describes other leaves, never obligations to move into target.
The editable_leaf is the authoritative surviving content: preserve every one of
its requirements. Do not infer cancellation from related or broader meanings.
cancellation_events, when present, concern only the named local pending issues.
They do not authorize dropping other content of editable_leaf. If an event seems
to conflict with surviving content, preserve that content and ask a local boundary
question rather than silently omitting it. Do not recreate the specific cancelled
output. A surviving output may retain its own necessary subject/account
association without restoring an account-identification output. depends_on means
semantic dependence, not merely sharing a table or needing an internal join.
New scope bindings belong only to the atomic contribution that needs them;
preserve a compound remainder's business conditions in meaning, without copying
the focus's new scope_columns to it. Do not weaken or drop those conditions.
answered_local_decisions are evidence, not pending decisions to reopen.
Return {"focus":{"meaning":"local contribution","granularity":"atomic|compound|undetermined",
"role":"output|modifier only for atomic", "columns":[], "scope_columns":[],
"computation":{"formula":"","operands":{},"time_scope":"","unit":"","status":"proposed|executable"}},
"remainders":[{same content fields}],
"question":{"dimension":"boundary|relation|field|operation|value","question":"one precise question",
"options":[{"label":"content only","columns":[]}],"multiple":false},
"issue_owners":{"existing pending ID":"focus|r1|r2"},
"relations":[{"source":"focus|r1|existing leaf ID","target":"...","kind":"applies_to|depends_on"}],
"coverage":"where every existing contribution survives"}.
Remainders are independent surviving contributions, not alternative choices.
First decide whether this is one coherent result. Prefer remainders=[] and refine
in place when the answer concerns attributes, operands, units or associated values
of the same result. Split only independently returnable business results or a
shared condition requiring its own scope. Always preserve the requested output,
even when focusing on a filter; a list of modifiers alone cannot replace a query.
If the editable leaf is compound, isolate an atomic contribution when possible.
Permission failure of a container is not evidence that all its descendants fail.
For a ratio question inside a profile, isolate the ratio and keep the other
profile requirements as a compound remainder. For a parameter of a single
complete calculation, use no remainder. Never force a full initial decomposition.
Code stages this partition; the real answer commits it. Do not omit independent
requirements or shared subject/time constraints. Compound nodes have no role and
only inherited dependencies. Fields are established dependencies, not speculative
candidate unions. Allocate all existing dependencies and pending IDs across parts.
Every focus must keep its actual subject/population, time anchor and reference
meaning. Do not put a shared loan-client subject ONLY into the remainder while
leaving the focus as an unbound "client's birthday". Preserve the explicit subject
in the focus, or materialize a shared anchor in a remainder with a proposed
depends_on edge. Shared reference is not the same as dependence on displaying
another output: age needs a birth-date operand, not the birthday output itself.
If asking an existing pending issue include question.issue_id. Do not repeat
already explicit intent. All relationships are proposed, not confirmed; do not
spread a local time condition across other contributions by rewriting their text.
Use read_only_context to avoid duplicating an existing condition as a new remainder.
When an answer supplies a threshold for the current condition, refine that same
condition rather than creating a second condition with the concrete threshold.
Keep an existing condition's attachment separate from the returned attributes.
Do not ask for additional eligibility conditions merely because the output says
"qualifying"; existing attached conditions already define qualification.
Business options omit columns; field content options require full dependencies.
For a field question omit options: code builds the authorized local menu.
When scope or business labels are insufficient, ask for semantic narrowing.
Never include cancellation, keep-unresolved, other/none, or replacement actions
as content options: code adds those bound to the focus. Open questions may omit
options. Question must address focus only. No SQL or permission reasons in public text.
''' + ISSUE_CONTRACT + CONTENT_TYPES + RELATION_TYPES + FOCUS_PLAN_TYPES

INTERPRET = '''Return one JSON object only. Update ONLY the supplied focus from the real public answer.
Close the current question using resolved, NEVER by listing its ID in issue_resolutions.
Compute optional issue_resolutions keys as local_pending_issues IDs MINUS
question.issue_id; if nothing remains, omit issue_resolutions or return {}.
Do not copy the answer's references to read-only sibling results into this focus.
A concrete value for this focus refines its existing condition, not a new result.
Return
{"meaning":"complete current local meaning","granularity":"atomic|compound|undetermined",
"role":"output|modifier only if atomic",
"computation":{"formula":"","operands":{},"time_scope":"","unit":"","status":"proposed|executable"}, "resolved":true,
"remaining_question":"if partial", "issues":[{"dimension":"boundary|relation|field|operation|value","question":"..."}],
"binding_suggestions":[],"output_exclusions":[]}.
Optional issue_resolutions maps IDs from local_pending_issues (except the current
question.issue_id) to {"status":"answered|partial|pending", "evidence":"exact quote
from event.answer.text or a selected label", "remaining_question":"required for partial"}.
Review these local issues against the answer. Close only fully answered issues;
for partially answered issues preserve only the remaining question. Omission keeps
an issue pending unchanged. Never close a combined formula/unit question if only
the formula was answered. New issues are only for genuinely new questions.
columns means internal data dependencies, not the list of returned attributes.
Use binding_suggestions only for atomic nodes. For compound/undetermined nodes
preserve all requested results in meaning and leave binding_suggestions empty.
Omit binding_suggestions when no binding change is intended. When changing an
atomic binding, include all still-needed dependencies; removing an established
dependency requires separate local clarification and is never inferred here.
Record requests such as 'do not return account IDs' in output_exclusions and meaning;
they do not remove IDs needed for joins or filters. Never return retired_columns
or retirement_reason; binding removal is not an answer-interpreter operation.
Optional columns omission preserves current and selected dependencies. Do not
return children, remainder content, owners, edges, action, or transaction metadata.
Code preserves remainders and commits the staged partition. A parameter answer
does not choose fields/coverage implicitly. Other contributions are read-only. Preserve shared
references and their unresolved scope. Never import a condition from another
contribution into this focus without this answer supporting it.
resolved means ONLY that the current question was answered, not that the node is complete.
Additional unanswered questions belong in issues even when resolved is true.
If the answer requests multiple independent results, preserve all of them in meaning
and return granularity=compound with no role. The next planning turn will split
that node using this answer as evidence; do not discard results or invent children.
Omit computation when unchanged to preserve existing formula, scope and units. No SQL.
''' + ISSUE_CONTRACT + CONTENT_TYPES + ANSWER_TYPES


def run(call, request, context, allowed, ask_user, *, max_turns=40, episode=None, save=lambda: None, semantic_review=False, answer_package=False):
    state = initial(request)
    episode = {} if episode is None else episode
    episode.update(method=VERSION, session=state, interactions=[], snapshots=[], transitions=[], proposals=[])

    def publish(new, event):
        nonlocal state
        episode['transitions'].append(dict(event=deepcopy(event), tree_before=deepcopy(state), tree_after=deepcopy(new),
            frontier_before=list(frontier(state)), frontier_after=list(frontier(new)), active_graph=graph(new)))
        state = new
        episode['session'] = state
        episode['snapshots'].append(deepcopy(state))
        save()

    def ask(q):
        if len(episode['interactions']) >= max_turns:
            raise Budget()
        history = [{'question': t['question'], 'answer': {k: t['answer'].get(k, [] if k == 'choices' else '')
                    for k in ('action', 'text', 'choices')}} for t in episode['interactions']]
        answer = ask_user(deepcopy(q), history)
        require(isinstance(answer, dict), 'Invalid public answer')
        episode['interactions'].append(dict(question=deepcopy(q), answer=deepcopy(answer)))
        save()
        return {k: answer.get(k, [] if k == 'choices' else '') for k in ('action', 'text', 'choices')}

    def finish(action, reason='', result=''):
        episode['delivery'] = dict(action=action, reason=reason, text=result,
                                   available_for_text2sql=action == 'deliver', sql_execution_evaluated=False)
        save()
        return episode['delivery']

    def open_answer(question):
        return ask(dict(question=question, focus=question, granularity='local_detail', options=[], multi_select=False))

    publish(state, 'initial')
    try:
        while True:
            step = next_step(state, context, allowed)
            kind = step['kind']
            if kind in ('unresolved', 'refuse'):
                return finish(kind, step['reason'])
            if kind == 'deliver':
                payload = dict(nodes=[{k: deepcopy(n.get(k, [])) for k in ('id', 'meaning', 'role', 'columns', 'scope_columns', 'computation', 'output_exclusions', 'answers')}
                                      for n in frontier(state).values()],
                               relations=[r for r in state['relations'] if r['status'] == 'confirmed'])
                def checked(raw):
                    require(isinstance(raw, dict) and set(raw) == {'text'} and text(raw['text']), 'Invalid rendering')
                    return raw['text']
                return finish('deliver', result=validated_call(call, 'tree_render', RENDER, payload, checked))
            if kind == 'relation':
                r = step['relation']
                q = menu('Confirm this relationship: ' + state['nodes'][r['source']]['meaning'] +
                         ' → ' + state['nodes'][r['target']]['meaning'] + ' (' + r['kind'] + ')',
                         ['Applies', 'Does not apply'], 'relation_confirmation')
                picked = choices(ask(q), q)
                if picked is None:
                    return finish('unresolved', 'relation_requires_restatement')
                new = deepcopy(state)
                next(x for x in new['relations'] if x == r)['status'] = 'confirmed' if picked == [0] else 'rejected'
                new['revision'] += 1
                publish(validate(new), 'relation_answer')
                continue
            target = step['target']
            if kind == 'permission':
                publish(cancel(state, target, automatic=True), dict(action='cancel', reason='no_authorized_local_plan'))
                continue
            if semantic_review:
                plan = prepare_reviewed(call, state, target, context, PLAN,
                    focus_payload(state, target, context), prepare,
                    episode.setdefault('partition_reviews', []))
                save()
                if plan is None:
                    return finish('unresolved', 'partition_semantics_rejected')
            else:
                plan = validated_call(call, 'tree_focus', PLAN,
                    focus_payload(state, target, context),
                    lambda raw: prepare(state, target, raw, context))
            episode['proposals'].append(deepcopy(plan))
            save()
            focus, question = plan['parts'][0], plan['question']
            empty_authorized_domain = False
            if question['dimension'] == 'field':
                # Field menus are code-owned. The model may name the semantic
                # gap, but it cannot invent bindings or expose schema columns.
                local_options = local_field_candidates(focus, context, allowed)
                empty_authorized_domain = local_field_domain(focus, context, allowed)['status'] == 'no_accessible_field'
                question['options'] = local_options
                question['multiple'] = True
            # Keep schema bindings private. Public menu text contains labels;
            # internal columns are used only after the user selects an option.
            internal_options = {o['label']: o.get('_internal_columns', o.get('columns', []))
                                for o in question['options']}
            public_options = [dict(label=o['label'], columns=[])
                              for o in question['options']]
            question['options'] = public_options
            # Outside confirmed constraints remain obligations. Parent-local
            # columns allocated to remainders must not poison this focus menu.
            external = dependencies(state, target, context, exclude_owner=True)
            common = node_dependencies(focus, context) | external
            binding = question['dimension'] == 'field'
            eligible = [o for o in question['options']
                        if not binding or safe(common | set(internal_options.get(o['label'], [])), context, allowed)]
            unavailable = (not safe(common, context, allowed)
                           or empty_authorized_domain
                           or (binding and bool(question['options']) and not eligible))
            if unavailable and focus['granularity'] == 'atomic':
                event = dict(action='cancel', answer={}, selected_columns=[], selected_labels=[],
                             reason='no_authorized_local_plan')
                publish(apply(state, plan, event, context), event)
                continue
            if unavailable:
                # A container's blocked bindings do not authorize cancelling
                # its contents or exposing their unfiltered field choices.
                question.update(dimension='boundary', options=[], multiple=False,
                                question='Which independent results should this part be split into: '
                                         + focus['meaning'])
                binding = False
                eligible = []
            labels = [o['label'] for o in eligible]
            labels += ['Cancel only: ' + focus['meaning'], 'Keep this contribution unresolved',
                       'Describe an acceptable replacement for this contribution']
            q = menu(question['question'], labels, 'focused_action', multiple=question['multiple'])
            # Open questions get their answer directly; actions remain available
            # after unanswerable, without making content up to fill a menu.
            answer = open_answer(question['question']) if not question['options'] else ask(q)
            selected = []
            action = 'answer'
            if answer['action'] != 'answer':
                if not question['options']:
                    answer = ask(q)
                picked = choices(answer, q)
                count = len(eligible)
                if picked is None:
                    # No executable option remains after permission/connection
                    # filtering. This experiment prunes the requirement
                    # directly; it does not expose the reason or ask whether
                    # the user still wants an inaccessible result.
                    action = 'cancel'
                elif any(i >= count for i in picked):
                    require(len(picked) == 1, 'Content choices and local actions are exclusive')
                    index = picked[0] - count
                    if index < 2:
                        action = 'cancel' if index == 0 else 'defer'
                    else:
                        answer = open_answer('Describe the replacement only for: ' + focus['meaning'])
                else:
                    selected = [eligible[i] for i in picked]
            event = dict(action=action, answer=answer,
                         selected_columns=sorted({c for o in selected for c in internal_options.get(o['label'], o['columns'])}),
                         selected_labels=[o['label'] for o in selected])
            if action != 'answer':
                publish(apply(state, plan, event, context), event)
                continue
            if answer['action'] != 'answer' and not selected:
                publish(apply(state, plan, {**event, 'action': 'defer'}, context), 'unanswerable_focus')
                continue
            if selected and binding and not safe(common | set(event['selected_columns']), context, allowed):
                return finish('unresolved', 'combined_selection_disconnected')
            interpreted_event, scope_review = review_answer_scope(call, state, event)
            if scope_review is not None:
                episode.setdefault('answer_scope_reviews', []).append(dict(event=deepcopy(event), review=scope_review))
                save()
                if not scope_review['remaining_answer'].strip():
                    # A repeated answer is not consent to cancel the surviving
                    # focus. Preserve it unresolved without restoring old outputs.
                    publish(apply(state, plan, {**event, 'action': 'defer'}, context), 'repeated_cancelled_answer')
                    continue
            answer_prompt, answer_apply = INTERPRET, apply
            if answer_package:
                from .answer_package import PROMPT, apply_package
                answer_prompt, answer_apply = PROMPT, apply_package
            new = validated_call(call, 'tree_focus_answer', answer_prompt,
                dict(focus=focus, question=question, event=interpreted_event, schema=context,
                     local_pending_issues=[deepcopy(state['issues'][iid]) for iid, owner in plan['owners'].items()
                                           if owner == 'focus'],
                     read_only_relations=plan['relations'],
                     **({'read_only_remainders': deepcopy(plan['parts'][1:])} if answer_package else {})),
                lambda raw: answer_apply(state, plan, interpreted_event, context, raw))
            if answer_package and semantic_review and new['audit'][-1].get('action') == 'answer_package':
                from .semantic_review import review
                package = new['audit'][-1]
                verdict = review(call, package['review_parent'],
                                 dict(parts=package['review_parts'], relations=package['review_relations']))
                episode.setdefault('answer_package_reviews', []).append(dict(review=verdict, target=target))
                save()
                if not verdict['accepted']:
                    return finish('unresolved', 'answer_package_semantics_rejected')
            publish(new, event)
    except Budget:
        return finish('unresolved', 'interaction_budget')
