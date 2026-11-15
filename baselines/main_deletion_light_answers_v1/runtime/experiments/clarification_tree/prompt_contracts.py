"""Explicit wire types shared by tree prompts (not business inference rules)."""

CONTENT_TYPES = '''
WIRE TYPES: Return one JSON object only, with double-quoted keys, no Markdown.
Notation such as a|b denotes alternatives: output exactly ONE listed value.
meaning/question/coverage are nonempty strings, never arrays or objects.
granularity is exactly "atomic", "compound", or "undetermined".
role is "output" or "modifier" for atomic; omit it or use null otherwise.
columns, scope_columns and binding_suggestions, where permitted by the response
contract, are arrays of UNIQUE schema-qualified strings, e.g. ["loan.loan_id",
"loan.date"]. Never use [{"column":"loan.loan_id","role":"..."}], table names,
SQL expressions, aliases, unknown fields, or comma-separated strings here.
Empty arrays are valid. Example names must be replaced by fields in the input schema.
output_exclusions is an array of nonempty business-language strings, e.g.
["Do not return account identifiers"], never field-descriptor objects.
computation is an optional JSON object, {} when unknown. Suggested properties:
formula/time_scope/unit are strings; operands is an object mapping semantic names
to descriptions or schema-qualified field strings (or arrays of these strings);
status, when present, is "proposed" or "executable". It is semantic explanation,
not a request to execute SQL. Omit an unchanged computation when interpreting answers.
Booleans are JSON true/false, not strings. Do not add keys absent from the contract.
'''

RELATION_TYPES = '''
relations is an array of objects with exactly source, target, kind, all strings.
kind is "applies_to" or "depends_on". Endpoints are supplied leaf IDs or the
aliases allowed by this prompt, never meanings or schema fields. Distinct endpoints
are required. applies_to goes from a condition to its affected result: an atomic
source must have role=modifier, and an atomic target must have role=output.
New relations are proposals; do not return a status or assume confirmation.
'''

FOCUS_PLAN_TYPES = '''
PLAN KEYS: required focus, remainders, question, coverage; optional issue_owners,
relations. focus is ONE content object; remainders is an array of content objects,
[] if none. Content keys: meaning and granularity required; role, columns,
scope_columns, computation, output_exclusions optional. Never nest remainders or
question inside focus. Aliases are code-owned: focus, r1, r2, ... by position.
For EVERY compound/undetermined content object, columns is a subset of the
editable leaf's existing columns and scope_columns is a subset of its existing
scope_columns. Empty parent arrays mean empty child arrays. Schema is a reference,
not evidence that a compound part's dependencies have been established.
question keys: dimension and question required; issue_id, options, multiple optional.
dimension is boundary/relation/field/operation/value (one literal).
options is an array of {"label":"nonempty unique business label","columns":[]}.
For field questions omit options: code owns field menus. For other dimensions
columns is empty or omitted. multiple is a boolean, never a count.
issue_id, if present, must name an editable pending issue allocated to focus and
have the same dimension. issue_owners maps EVERY allowed_issue_owner_id to one of
focus/r1/r2/... exactly once. With no pending IDs return {}. No outside IDs.
'''

ANSWER_TYPES = '''
ANSWER KEYS: meaning, granularity, resolved required. Optional role, computation,
binding_suggestions, output_exclusions, remaining_question, issues, issue_resolutions.
Do not return children, owners, edges, scope_columns, retired_columns, or retirement_reason.
resolved is a boolean about the CURRENT question only. If false, remaining_question
must be a nonempty string. If true, omit remaining_question or use "".
issues is an array of NEW questions, each with exactly dimension and question;
dimension is boundary/relation/field/operation/value. Do not put existing IDs here.
issue_resolutions is an object keyed only by local_pending_issues IDs other than
question.issue_id. Each value has status and evidence; remaining_question is optional.
status is "answered", "partial", or "pending". For answered/partial, evidence is a
nonempty EXACT substring of event.answer.text or a selected label. Partial requires
a nonempty remaining_question. Pending may use evidence="" and leaves the issue unchanged.
Omitted issue IDs remain unchanged. Use {} if there are no other local resolutions.
Example binding shape: "binding_suggestions":["loan.loan_id","loan.date"].
For compound/undetermined omit binding_suggestions or use []; never guess bindings.
'''
