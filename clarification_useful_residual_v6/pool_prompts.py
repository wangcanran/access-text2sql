"""One decomposition contract for the opt-in pool, without legacy overrides."""
from .prompts import PLAN

_OLD_EXAMPLE = ('"binding_claims":[{"meaning":"Employee phone numbers",'
                '"columns":["employee.phone"]}]')
_OLD_SHAPE = 'Every node has exactly the fields shown. Do NOT return role, edges, edge kinds,'
if PLAN.count(_OLD_EXAMPLE) != 1 or PLAN.count(_OLD_SHAPE) != 1:
    raise ValueError('Inherited decomposition prompt changed; review v10 wire contract')

DECOMPOSE = PLAN.replace(
    _OLD_EXAMPLE,
    '"binding_claims":[{"meaning":"Employee phone numbers","columns":["employee.phone"],'
    '"use":"returned","state":"bound","obligation_ids":["o1"]}]'
).replace(
    _OLD_SHAPE,
    'Every node has exactly the fields shown in the JSON example above, including '
    'use, state and obligation_ids in each binding_claim. Do NOT return role, '
    'edges, edge kinds,') + '''
CLAIM AND LINEAGE CONTRACT:
Each binding_claim explicitly supplies meaning, columns, obligation_ids,
use=returned|subject|condition|operand, and state=bound|open. For an inherited
claim, keep the referenced parent's use exactly; code rejects type changes.
For a NEW claim use obligation_ids=[]. New fully specified literal
conditions/operands may be bound with columns=[]; returned attributes and subject
identities need real schema columns. Include all required calculation and
relationship/join operands, including association-table endpoints needed to link
the requested output to its specified subject. Sharing a higher-level entity
does not establish a more specific requested relationship.

CANDIDATE LABEL CONTRACT:
candidates maps exact schema column IDs to short BUSINESS labels. Keep the
physical ID only as the JSON key. The label value must express what that field
means to a requester; never repeat the table.column ID as the label or prefix
the business description with it. Use the supplied schema descriptions to
distinguish options. If no schema-backed choice fits the pending field
question, return an empty candidates map; code handles the empty domain.

Route EVERY inherited contribution ID to descendants of its own parent. Several
claims may refine one contribution, but cannot change its business use or lose
its confirmed dependencies. Do not merge unrelated contributions that share a
column. New contributions use []; code assigns their stable contribution IDs.

PARENT AND DECISION INVENTORY CHECK before returning JSON:
For every column in a parent's columns, check that it appears in at least one
descendant's columns OR as a key in retired_dependencies with a nonempty
business reason. If a key is needed to associate a returned name with a
condition, carry it on the relevant relationship provider; never silently omit
it. Retire a genuinely superseded speculative key only when the remaining
request no longer needs that association or identity.
The exact keys of parents are the complete required parent set. For EACH parent
key, include at least one nodes entry whose parent equals that key, including
the parent targeted by none_event. NONE only marks a descendant for later
code-owned pruning; it never authorizes omitting that parent at decomposition.
Use decision_ids ONLY from pending_decisions in this payload and only on a
descendant of that decision's owner_id. A decision visible in actual_answers,
settled_questions, historical nodes or old claims but absent from
pending_decisions is already closed: do not copy its ID into a new node.
For every pending_decisions entry, route its ID to the appropriate descendant
of its own parent. Use decision_ids=[] for a child with no pending question.
Returned information stays on an output child. An existing operand used only
for ranking/filtering is NOT an extra returned result: put it on a requires
provider and list its actual consumers in affects. Do not invent a returned
metric merely because its value is needed internally. If an internal provider
is also needed for a requested returned result, preserve the returned ID on the
output and create a separate operand claim with [] on the provider.

Bound fields remain mandatory even if access fails. A known name is not replaced
by an entity ID. A time predicate keeps its date operand. Keep unknown business
choices open, with their current question IDs. A user answer settles its asked
purpose, not other choices. Permission decisions are code-owned internal checks:
do not create permission questions or ask the user to approve deletion.

For each supplied subject_obligation preserve its exact meaning string and all
original columns on exactly one non-relation scope provider. Every affected
output must have a requires path to that provider, expressed through affects.
A generic entity key or join cannot replace a population-defining condition.
Each output descendant must retain every applicable inherited subject provider.

For NONE, route the actually asked IDs to their contribution leaf. Other pending
field/value/operation questions may stay there ONLY when all its claims refer
to the SAME single inherited contribution ID: losing the chosen field also
invalidates that field's value. Multiple independent contributions MUST split
before pruning. Do not combine independent results on the rejected leaf. Code
locates the seed by asked ID and applies the dependency closure; never return
deletion flags. Preserve unrelated pending decisions on their own descendants.
'''
