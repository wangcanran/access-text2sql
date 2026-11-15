UPDATE = '''Apply ONE event to the active local graph. Return JSON only:
{"updates":{"f1":[{"id":"p1","text":"current requirement","columns":["t.c"],
"questions":{"field":"Which information?"},"candidates":{"t.c":"business label"}}]},
"edges":[["p1","f2","applies_to"]],
"retire_columns":{"f1":["t.old"]}}

Only updates is required. Keys are editable parent IDs; omit unchanged parents.
Each child requires only id, text, columns. Use unique local aliases such as p1,p2.
Optional role (output/modifier), kind (attribute/calculation/relation/instruction)
and tables inherit the parent. questions/candidates default empty.
Text expresses current meaning; no quotes, offsets or witnesses. A modifier stays
modifier. output means a returned value, not a filter operand, entity heading or
its count. A requested profile is an output even with several values.
Split only independently actionable contributions; separate output-specific
calculation methods. A shared modifier expresses the same constraint on every target.

ANSWER: Change only the asked purpose and meanings affected by the answer.
Keep selected fields in children serving that purpose. Input decisions have stable
IDs. Code closes event.asked_decisions and inherits EVERY other pending decision,
even on revised nodes. Omission never resolves one. Child questions adds NEW
uncertainties only. Reword existing questions through question_changes, keeping
their meaning and type. Do not repeat a just-answered question as a new one.
Optional top-level question_changes contains ONLY affected old decision IDs:
{"d2":{"resolved":"The selected option explicitly supplies the missing fact"},
 "d3":{"to":["p2"],"question":"Reworded remaining question"}}.
Resolve related decisions only from current answer/selected-option evidence,
never from graph reachability or shared columns alone. For partial answers, keep
the asked ID using to/question for its unanswered remainder. On splits, to routes
old decisions to appropriate surviving children; provide it when ownership is
ambiguous. Do not change a decision's dimension through rewording.
Preserve unresolved meaning in BOTH text and questions: selecting fields does
not decide coverage, quantities, formulas or thresholds. A business scope is an
entity/category choice; which/how many members to include remains a value detail.
Do not infer 'all' from a field selection. New answer-introduced dependencies and
uncertainties still need columns and questions; do not assume they are authorized.
retire_columns names old dependencies superseded by a field/scope choice.
Code inherits unwithdrawn dependencies for one child; for splits, assign them
to the children that need them.

REMOVAL: [] removes a whole parent. In a compound parent, withdraw only the event
purpose and its dependent parameters; return the independent surviving meanings.
The withdrawal is real, not an unspecified version of the same requirement.
Split independent surviving outputs into separate children in this transaction,
so later withdrawals need not rewrite the compound profile again. Keep their
subject/attachment explicit in text or shared context edges; never invent an
extra profile-heading output alongside its returned values.
purpose.remaining_requirements is a hint, not an exhaustive survivor list.
Every preserve_outputs parent must keep an output child, even when its returned
fields are unspecified: deleting its predicate cannot delete the return request.
Keep other predicates AS PREDICATES, not extra outputs; never restore withdrawn intent.
Remove qualifications exclusive to deleted outputs. Review surviving shared
modifiers in review_ids in this SAME update: narrow text AND questions to surviving
targets. If their meaning stays unchanged, return their surviving edges instead
of recopying the node. Do not copy questions mentioning deleted outputs. questions/columns
are the survivor snapshot.
Code retires old-only dependencies (including exclusive joins); omit retire_columns.
No surviving output means no query; never invent another objective.

questions keys: scope, field, operation, value, for unresolved decisions only.
Business entities/categories/fields need menus; only operations/values ask details.
An information category does not determine a field. Map every determined operand
and required join key, including those introduced by an answer. columns and
candidates keys must be literal schema column IDs, never formulas. Candidates
are relevant field alternatives without access filtering, not selected dependencies.
A context-only instruction needs no invented identifier.

Return edges touching split/revised nodes with p IDs and existing boundary IDs.
applies_to: source restricts/organizes target; context: source relies on target.
Code retains unchanged edges and same-role one-to-one attachments. Preserve shared
attachments to surviving targets. Boundary nodes are read-only; connectivity alone
never licenses deletion. Preserve comparisons, quantities, AND/OR, negation and
scope. No inferred details, SQL, access explanations or external-workaround advice.
'''

RENDER = '''Write one natural-language database request from the active requirements
and their relations. Return JSON only: {"request":"..."}. Return exactly the output
contributions; apply modifiers to the subjects identified by the active edges.
Use only active nodes, active edges and their literal dependencies. If a deletion
removed an attachment or anchor, do not retain a dangling reference to that
removed requirement; restate the surviving output with its remaining explicit
subject, or leave its existing ambiguity unresolved. Do not invent a replacement
entity, field, value or condition. A deleted node and historical intent are not
available evidence. Keep calculations, comparisons, quantifiers, AND/OR and
unresolved wording intact. Do not add outputs from filter operands or resolve
ambiguity. Use the input language.
'''

DECISION = '''Locate ONE next business decision in source, using current_request.
The source may contain several outputs and conditions. For scope/field decisions,
use question_focus to select ONE unresolved purpose, not every output in source.
For permission, pending_columns determines the purpose. Locate the smallest
contribution USING a pending dependency. A population restriction belongs to
that modifier, not automatically to all returned attributes or their open choices.
Do not bundle independent purposes just because they share the same owner.
Return JSON: {"requirement":"local purpose","role":"output|modifier",
"kind":"attribute|calculation|relation|instruction","scope_status":"fixed|unresolved",
"scope_tables":["table"],"required_columns":["table.column"],
"question":"Which business information/entity should be used?",
"field_candidates":[{"column":"table.column","label":"business meaning"}],
"remaining_requirements":["independent remainder inside this source"]}.
source is the sole owner being decomposed. read_only_context provides related
subjects/qualifications, not additional remainders owned by source. Its nodes
remain independently retained by code. Source tables are provisional: choose
only the anchors supported by this purpose's CURRENT meaning.
No quotes or offsets required. output means returned value, modifier means filter
or result instruction. A filter operand does not become an output. Locate ONE
output or ONE filter measure; never mix their candidate domains. A named subject
with unspecified returned attributes has FIXED scope and needs a FIELD menu.
unresolved means the business entity or attachment itself is uncertain, not that
its field is unspecified. fixed scope names THIS purpose's semantic search scope,
not mandatory simultaneous joins; exclude tables used solely by its remainder.
unresolved scope lists plausible entities and has no field_candidates. Include
scope_choices: [{"text":"business option","required_columns":["table.column"]}]
for that scope menu in this same response. Each option expresses one relevant
business alternative with actual supporting columns; no access filtering.
A fixed
scope needs all relevant literal schema fields in field_candidates, even if its
text currently names a field. Do not return an empty menu just because the text
is specific: this call prepares a pending field choice. No access filtering. A
permission purpose must include at least one pending column in required_columns;
determined dependencies must be literal schema columns. Candidates are
alternatives, not requirements. Do not ask values or formulas in this call.
remaining_requirements describes only contributions that still make sense after
withdrawing the WHOLE asked requirement. A filter's threshold, operator, time
window and denominator are dependent parameters, NEVER independent remainders.
If the source is just one predicate or one output, use []; a compound source may
retain other outputs/predicates. An empty remainder can enable code-only deletion
of an already classified independent unit; never omit independent contributions.
Keep one/several/all ambiguity. No permissions in public wording,
extra outputs or external-workaround advice.
'''
