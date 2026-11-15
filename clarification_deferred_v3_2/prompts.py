import json


INITIAL_QUESTION_POLICY = '''
QUESTION DISCOVERY BOUNDARY: distinguish a missing requested fact from an
optional refinement that could make a query more specific. Register only the
former. Each question must fill a slot that the current request actually leaves
open; do not create new grouping, partitioning, entity, join, time restriction,
tie policy or exclusion choices just because the request does not mention them.
An unstated restriction is not a missing restriction. Ranking/comparison uses
the stated subject domain and stated qualifications; it does not by itself
require a new partitioning choice. A named category is a qualification, not an
invitation to add another grouping dimension. If a category value, requested
attribute, ranking measure/rank, formula or period is actually unspecified, ask
that fact and preserve each independent unanswered fact. Do not answer missing
facts yourself. Do not register the same semantic purpose twice under different
dimensions or through both its general phrase and its more specific parameter.
The goal is a faithful natural-language Text2SQL request at user specificity,
not exhaustive SQL implementation decisions. Apply this boundary before writing
open_choices or missing/question_focus; code later preserves these commitments.
A generic requested summary/statistic is ONE operation question: ask the intended
measure/formula including its inputs together. Do not additionally register a
field question to choose some arbitrary numeric attribute to aggregate. A count
or percentage may need entity counts rather than any measured numeric attribute.
Separate field decisions are warranted only for independently requested returned
attributes or explicitly unspecified operands independent of that operation.
'''


# A type schema cannot be mistaken for an example business answer. The previous
# instance example leaked its placeholder into public question titles, and left
# the element type of an empty remainder array unspecified.
DECISION_SCHEMA = {
    'type': 'object',
    'required': ['requirement', 'role', 'kind', 'scope_status', 'scope_tables',
                 'required_columns', 'question', 'field_candidates',
                 'remaining_requirements', 'clarification'],
    'additionalProperties': False,
    'properties': {
        'requirement': {'type': 'string', 'minLength': 1,
                        'description': 'Actual local business contribution from the current source'},
        'role': {'enum': ['output', 'modifier']},
        'kind': {'enum': ['attribute', 'calculation', 'relation', 'instruction']},
        'scope_status': {'enum': ['fixed', 'unresolved']},
        'scope_tables': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}},
        'required_columns': {'type': 'array', 'items': {'type': 'string'}},
        'question': {'type': 'string', 'minLength': 1},
        'field_candidates': {'type': 'array', 'items': {
            'type': 'object', 'required': ['column', 'label'], 'additionalProperties': False,
            'properties': {'column': {'type': 'string'}, 'label': {'type': 'string'}}}},
        'remaining_requirements': {'type': 'array', 'items': {'type': 'string', 'minLength': 1}},
        'clarification': {'type': 'object', 'required': ['dimension'], 'additionalProperties': False,
                          'properties': {'dimension': {'enum': ['scope', 'field', 'operation', 'value']}}},
        'scope_choices': {'type': 'array', 'items': {
            'type': 'object', 'required': ['text', 'required_columns'], 'additionalProperties': False,
            'properties': {'text': {'type': 'string'}, 'required_columns': {
                'type': 'array', 'items': {'type': 'string'}, 'minItems': 1}}}},
    },
}

DECISION_CONTRACT = '''Compile ONE local clarification purpose. Return only a JSON INSTANCE
conforming to the following JSON Schema (do not return the schema itself):
''' + json.dumps(DECISION_SCHEMA, separators=(',', ':')) + '''
All required keys must be present. remaining_requirements is an array of plain
strings, never node objects. clarification contains ONLY dimension; no aliases.

Use question_focus to select ONE existing pending purpose. For permission_decision,
locate the smallest contribution using at least one pending_columns dependency.
source is the sole editable owner. read_only_context supplies established related
subjects/conditions, not independent remainders owned by source. Do not reinterpret
the question as a new task. A filter or sorting instruction is a modifier; its
operand does not become a returned output.

scope means the business entity/population or attachment is genuinely uncertain.
A named subject with unspecified attributes has FIXED scope and a field question.
Do not invent a population/partition choice merely because a ranking compares
records: preserve the stated qualifying conditions and ask the actual missing
rank, ordering measure, or category. Do not add optional refinements.
For unresolved scope, field_candidates=[]; additionally return scope_choices as
[{"text":"business alternative","required_columns":["table.column"]}].
For fixed field purposes, include all relevant literal schema-backed attribute
alternatives in field_candidates, with business labels. Include names as well as
IDs where both fit. An entity identifier menu MUST include locally available
human-readable names alongside keys, not interpret 'identifier' as numeric keys
only. The planner's existing candidates are provisional, NOT an exhaustive domain:
read all relevant local schema meanings. Candidate alternatives are NOT simultaneous required_columns.
scope_tables names this purpose's search domain, not mandatory joins; exclude
tables used solely by independent remainders. required_columns are actual known
dependencies, not guesses about a still-open choice. Use literal schema IDs only.
remaining_requirements contains only independent contributions that still make
sense if the WHOLE asked contribution is removed. A predicate's operator, value,
time window and denominator are dependent parameters, not independent remainders.
For one indivisible contribution use []. Never omit independent source outputs.

DEFAULT POLICY: A VALID ANSWER SETTLES THE CURRENT QUESTION.
No followups, continue_decisions, or speculative post-answer completeness checks.
The dimension describes what the proposed question actually answers: scope for
business entity/population, field for literal returned/input attributes,
operation for statistic/formula/comparison, value for literal parameters.
For operation/value use field_candidates=[] and an open business question.
An unspecified summary statistic is an operation, not a menu of height/weight.
Reclassify a misrouted operation/value purpose before any user-facing menu.

Design the question to resolve its stated purpose in one valid answer. If the
missing fact is the category value, ask which category, not which column stores
it. Selecting an entity must not silently choose its numeric primary key as the
returned attribute. Include human-readable names when the current public request
could mean names as well as IDs, supported by the local schema meanings.
Other pending_decisions are independent questions, retained automatically.
Do not combine their purposes into the current question or invent more tasks.
Use business labels and schema-backed candidates; authorization is code-owned.
Return JSON only.
'''

UPDATE_CONTRACT = '''
DEFAULT ANSWER POLICY (overrides legacy question-update instructions):
event.asked_decisions are SETTLED by the current valid answer. Write the answer
into child node meaning, columns and edges at the level of specificity supplied.
Do not judge the answer insufficient, retain/reword the asked ID, ask for more
details of this purpose, or create another question under a new ID/dimension.
Do not invent details, thresholds or formulas that the answer did not supply.
The final product is a natural-language Text2SQL request, not a fully executable
graph; the supplied answer is authoritative at its stated abstraction level.

ALL OTHER existing decision IDs remain pending unless the answer clearly
settles them too. When a dense answer covers additional current decisions,
list their code-owned IDs in settled_decisions:["d1","d3"]. The IDs must
come from the supplied pending decision list; do not infer or invent them.
The answer coverage reviewer checks that each listed decision is supported.
On splits route remaining decisions with
pending:["d2"] or question_changes:{"d2":{"to":["p2"]}} within descendants of
their own parent. Never resolve/reword another question merely because its owner
is connected, its dimension matches, or the current answer mentions it.
Child questions are routing hints for existing decisions ONLY; preferably omit
them and use pending IDs. They cannot introduce new uncertainty. Existing fields,
operations and values not asked now remain pending through their own identities.
Do not return followup_routes/followups/continue_decisions. Update nodes and
edges normally; preserve independent business contributions and permissions.
Return JSON only.
'''

REVIEW_POLICY = '''
The session policy is valid_answer_closes_current_question. Actual accepted
answers settle their asked IDs; do not demand additional clarification of those
same purposes at a finer granularity or reject merely because the graph is not
fully executable. The final target is natural-language Text2SQL input. Still
reject factual contradictions, invented facts, unjustified deletion, permission
violations or omitted independent requirements. Other unanswered IDs must remain
pending. Answer settlement is not permission to invent missing detail.
Return JSON only.
'''

ANSWER_UPDATE = '''Apply the accepted answer to the editable local graph. Return JSON only:
{"updates":{"f1":[{"id":"p1","text":"current requirement",
"columns":["table.column"],"pending":["d2"]}]},
"edges":[["p1","f2","applies_to"]],
"question_changes":{"d2":{"to":["p1"]}}}.
Only updates is required. Keys must be editable parent IDs; omit unchanged parents.
Each child requires id, text, columns. Optional role, kind, tables inherit the
parent. Optional questions maps scope/field/operation/value to strings, but only
as routing hints for EXISTING decisions. Optional candidates maps schema column
IDs to business labels for remaining field decisions. Unique p aliases identify
children; code allocates stable IDs. A modifier remains a modifier.
Split independent contributions if useful, preserve outputs and their meaning.
Keep selected fields in children serving the answered purpose. Use schema IDs
only for columns/candidates, never formulas. columns are actual requirements,
not alternative candidates. Preserve necessary join keys and subject conditions.
Optional retire_columns:{"f1":["table.old"]} names superseded dependencies.
Edges use child aliases or existing boundary IDs; applies_to means source
restricts target, context means source relies on target. Preserve attachments
to untouched nodes. Boundary nodes are read-only. An answer cannot delete a
parent or independent output; no [] updates. No SQL or permission explanations.
''' + UPDATE_CONTRACT
