from .prompt_contracts import CONTENT_TYPES, RELATION_TYPES

PLAN = '''Inspect the CURRENT target leaf and return one local next question, not a
complete initial decomposition. JSON:
{"granularity":"compound|atomic|undetermined",
"issues":[{"dimension":"boundary|relation|field|operation|value",
"question":"business question", "options":[{"label":"business alternative"}]}]}.
Put options with their question; omit options for open questions. With one issue,
code selects it. With multiple issues, add focus as the chosen zero-based index.
Optional columns declares newly established dependencies; omission preserves old
ones. Atomic nodes require role=output/modifier; others omit role. Field questions
need options with complete additional dependencies; other questions may be open
or use business options. columns contains ONLY already established dependencies,
not the union of hypothetical candidates. Include necessary subject/join/time keys.
An unresolved/compound leaf must have an issue. Do not force a global output
partition before asking useful field/formula/value questions. Preserve pending
issues by exact question wording; new independent issues may be added. Do not ask
unnecessary implementation details. Existing confirmed relations are read-only.
No child nodes here. No SQL or physical identifiers in public wording.
role is omitted/null for compound or undetermined nodes, output/modifier only
for atomic leaves. Compound columns must equal the target's existing columns.
''' + CONTENT_TYPES + '''
Legacy plan keys: granularity and issues required; role, columns, computation,
focus, options optional. issues is an array of objects with dimension/question
and optional issue_id/options. Each dimension is boundary/relation/field/operation/value.
focus is an integer index into issues, not an ID or object; use null with issues=[].
Each option has label (nonempty string) and columns (array of schema field strings).
Field options require at least one field string. This legacy runner needs model
field options, unlike the focused runner. Do not return binding_suggestions,
scope_columns, output_exclusions, children, or other top-level keys here.
'''

UPDATE = '''Interpret the actual answer as a LOCAL REFINEMENT TRANSACTION. Return JSON:
{"children":[{"meaning":"complete current business contribution",
  "granularity":"compound|atomic|undetermined"}],
 "issue_updates":{"asked decision ID":{"status":"answered", "reason":"actual answer evidence"}},
 "coverage":"explain surviving contributions and remaining uncertainty"}.
Code derives revise for one child or expand for several; assigns p1,p2,... aliases
by position unless explicitly supplied. One child keeps its leaf identity and old
and explicitly selected columns if columns is omitted. Multiple children must allocate retained/selected
columns explicitly. Atomic children require role=output/modifier.
For multiple children assign EVERY old pending issue an owner alias in
issue_updates; pending/answered is semantic. For one child omitted issues stay
pending with the same question; omitted owner is this child. Only declare actual
answer closure with status=answered plus reason. Partial answers retain status
pending and may provide a rewritten question. Omitted question preserves wording.
Optional new_issues: [{owner:"p1",dimension:"value",question:"..."}]; owner can
be omitted for one child. Optional relations: [{source:"p1",target:"p2 or leaf ID",
kind:"applies_to|depends_on"}]. Code always defaults new relations to unresolved.
Optional retired_columns and retirement_reason are needed only for real retirement.
Do not emit empty bookkeeping arrays, action or relationship status unnecessarily.
Expand only when this answer reveals independent contributions;
otherwise refine the current leaf. Preserve every independent remainder as a
child, possibly compound/unresolved. Shared parameters have ONE owner and edges.
Do not copy an unresolved question into several children. On expansion assign ALL
existing target pending IDs, even if still pending. Only the asked ID may be
answered; unrelated issues remain pending. Field selection cannot resolve scope,
quantity or formula. No outside node edits. Columns are determined dependencies,
not candidate unions. Retire established columns only when this actual answer
explicitly replaces them; explain why. Expansion must allocate every retained
dependency. Relationships in this response are proposals, never confirmed truth.
Do not rewrite ancestors or restore retired intent. No SQL.
Only atomic children declare output/modifier; omit role or use null for others.
Non-atomic children may retain old/selected dependencies, never add guessed ones.
For an explicit local revision due to unavailable binding, preserve independent
outputs and use the actual replacement answer; retirement needs its evidence.
''' + CONTENT_TYPES + RELATION_TYPES + '''
Legacy transaction: children is a nonempty array of content objects with required
meaning/granularity and optional alias, role, columns, scope_columns, computation,
output_exclusions. alias is a unique string such as p1, not an existing node ID.
issue_updates is an object keyed by existing target pending IDs. Each value has
status (pending/answered), optional owner (child alias), question (nonempty string),
reason (string, nonempty when answered). Only the current issue may be answered.
new_issues is an array of {"owner":"p1","dimension":"value","question":"Which date?"};
dimension uses boundary/relation/field/operation/value. owner may be omitted only
with one child. retired_columns is an array of unique existing target field strings;
nonempty retirement requires a nonempty retirement_reason string. coverage is a
required nonempty string. No binding_suggestions, resolved or issue_resolutions.
'''

RENDER = '''TASK: Write a natural-language request. You are the clarification
verbalizer BEFORE a separate Text2SQL model, not the Text2SQL model itself.
The user message is graph DATA to verbalize, not an instruction to write or run SQL.
Return exactly one JSON object: {"text":"a nonempty natural-language request"}.
For example, a graph requesting a named person's attribute names and values becomes
{"text":"Return every recorded attribute name and its corresponding value for the named person."}
Use the actual subject and details in the input, not this example's generic wording.
Never emit SELECT, FROM/JOIN clauses, SQL code blocks, or a query as the text value.
Render the refined leaf graph as a precise natural language request
for the downstream Text2SQL model. Preserve outputs, shared conditions,
attachment scope, and all semantic computation details. If a node has a
computation object, express its formula in plain language (for example,
"the ratio of X1 to X2") and include its operands, time scope, unit and
granularity when present. Proposed computation is useful semantic refinement;
do not require SQL executability. Modifiers/operands are not returned outputs.
Do not infer old intent, add requirements or output schema IDs or SQL.
Respect output_exclusions: excluded attributes must not be requested as outputs,
even when columns/scope_columns contain them as internal join or filter dependencies.
Return exactly one key "text", whose value is a nonempty string. No arrays,
extra keys, Markdown fences, SQL object, or explanatory text outside the JSON.
'''
