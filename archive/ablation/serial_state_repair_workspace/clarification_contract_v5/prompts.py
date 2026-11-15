ANSWER_TYPES = '''
TYPED QUESTION CONTRACT (required, replaces legacy dimension-only inference):
Each open_choices entry additionally has answer_kind, exactly one of:
entity_value: an actual entity locator VALUE such as a person's name or record ID;
literal_value: actual date, period, category value, threshold or parameter;
attribute: choose the returned attribute or an independently requested operand;
operation: statistic/formula/comparison/rank including its inputs;
entity_kind: choose genuinely different business entity classes;
relationship: choose how a known modifier relates to known output(s).
Code derives dimension and the menu/open protocol from this type. A question
about WHICH player/country/record is entity_value, not entity_kind merely because
an entity's value is unknown. Choosing a name-column cannot supply a person's
name. A date-range/condition value is literal_value; never invent sample ranges.
When the missing condition includes both the attribute and its value, ask an
open question for the complete business condition as literal_value. Do not force
an extra attribute menu before learning the actual condition. A single valid
answer settles the asked purpose at user specificity; do not plan followups.
Do not invent independent conditions absent from the public request.
'''

MAPPING_CONTRACT = '''
Return the usual items/edges JSON, with these REQUIRED additions to every item:
1. answer_kinds: a map from every question_focus key to one answer_kind from the
typed contract. Return {} if no question_focus. Code derives routing dimensions.
2. binding_claims: a nonempty array of objects, each with exactly
{"meaning":"one business contribution", "use":"returned", "state":"bound",
 "columns":["table.column"]}.
use is returned|operand|condition|subject; state is bound|open.
A returned claim states what will actually be returned, not how its subject is
located. A subject identifier cannot replace a requested page/name/element.
For a known returned attribute/formula include the real schema dependencies in
bound claims even if other parameters remain unknown. A formula includes all
input columns. Entity counts may use a real entity key as their count operand.
Literal operands and conditions that are fully specified by the answer (for
example top 3, a numeric threshold, sort direction, or a date literal) may be
state=bound with columns=[]. They must still have a precise meaning. A returned
attribute or subject identity may never be bound without real schema columns.
For a genuinely unselected returned field or formula input, use state=open,
columns=[] and retain its field/operation question. Literal values can remain
unanswered while their known field binding is bound. No alternative candidates
as mandatory claims. For an open predicate with known schema operand but unknown
literal value, keep its condition-column dependencies in an open condition
claim. Every output item needs a returned claim. Modifiers have
condition/subject claims; they must not contain returned claims. A compound item
may have several claims for its distinct contributions. Schema-ground only;
do not omit dependencies because their access may fail. Code owns permissions.
'''

UPDATE_BINDINGS = '''
Every child in updates also REQUIRES binding_claims in the same shape:
[{"meaning":"actual returned information", "use":"returned",
"state":"bound", "columns":["table.column"]}].
use=returned|operand|condition|subject; state=bound|open. Preserve the distinct
existing binding claims supplied in input; update only their affected meaning.
Known requested attributes and formula operands must be bound to real columns.
Fully specified literal operands/conditions such as top 3, thresholds, sort
directions, and date literals may be bound with columns=[]; returned and subject
claims still require real schema columns.
A locator/name used only to select an entity is a subject claim, NOT the returned
attribute. A returned page must bind its page field, even if inaccessible.
For still-pending field/operation decisions use open with no columns. When those
decisions are answered, bind their claims. Do not invent further questions or
replace actual requested outputs with just entity identification. Include every
bound claim's columns in the child's columns. Code will enforce this as well.
For a value answer, keep the previously confirmed returned object and its bound
returned columns. The answer may specify how to identify or filter that object;
it does not replace the object with an entity mentioned in the answer. Do not
retire confirmed columns in a value answer. A value answer can settle the asked
condition without reopening the output choice.
'''

DECOMPOSE_BINDINGS = '''
Each plan node additionally returns binding_claims as supplied for the current
parents. Each claim supplies meaning (business contribution text) and columns
(real schema columns). Code compiles use and state from the node role/kind and
column presence; use is returned|operand|condition|subject and state is
bound|open in the canonical graph. Do not invent labels such as "subject binding".
Split claims among appropriate leaves;
do not drop independent returned contributions. Bound claims are real required
dependencies even if access fails. An open returned claim retains its existing
field/operation question. Every output has a returned claim, not just a subject.
The input subject_obligations list is a preservation contract. For each parent,
identify the business population/locator described by its meaning and columns.
Create a non-relation modifier leaf for its population or locator condition;
keep its distinguishing schema columns on that leaf, even if access is denied.
Connect each affected output to that leaf with [output,subject_leaf,"requires"].
A generic entity-ID leaf or a table-join relation does not substitute for a
specific population restriction such as "outpatients". If Patient.Admission
defines the outpatient population, the leaf using Patient.Admission is a subject
provider, while a separate Patient.ID/Laboratory.ID leaf is only their join.
Do not attach the outpatient leaf with applies_to: losing it changes the
population, so the output must be removed. Use applies_to only for a truly
optional qualification whose removal leaves the requested output's identity
unchanged.
Before returning the plan, check every output descendant of a parent with a
subject_obligation: it must have a requires path to each applicable subject
provider (directly or through required providers). A subject provider with no
such path is an invalid split, not an independent condition; code rejects the
plan rather than silently broadening the output population.
Route each supplied decision_id only under its original dimension. You may
rephrase the question for its child; exact wording need not be copied. Code uses
the decision ledger as the authority for open-claim identity and type.
If a NONE event is supplied, split its exact asked purpose from all other pending
decisions BEFORE deletion. The seed leaf may own only the asked IDs. Other
decisions belong to separate leaves; explicit requires can propagate necessary
operand/subject loss. Optional filters use applies_to and cannot cancel outputs.
Never merge the whole old compound parent into the rejected leaf. Code owns the
deletion closure and allocation of stable node/question IDs.
'''

WITHDRAW = '''Identify the leaf that owns this actual NONE/empty-menu purpose.
Return JSON only: {"blocked_aliases":["p1"],"reason":"business reason"}.
Choose only the supplied eligible_seed_aliases. They are descendants of the
questioned parent, contain at least one asked decision ID, and no other pending
decision IDs. Do not seed other leaves, entire parent containers, connected
outputs or shared operands. Code propagates necessary loss over requires edges.
'''
