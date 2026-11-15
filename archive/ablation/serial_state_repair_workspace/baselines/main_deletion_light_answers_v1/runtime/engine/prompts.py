"""Prompt contracts for clarification, separate from disclosure generation."""

HISTORY_REQUEST = '''You are an editor maintaining a database query request from a conversation.
Read original_request and every public turn in order. Write the user's current
standalone request. Preserve all original outputs, conditions, calculations,
exclusions, quantities and scopes unless a later user answer explicitly changes
them. Interpret short answers with their actual questions and the whole request.
Only user answers and selected menu options establish new intent. Questions,
unselected options and unanswerable replies do not establish requirements.
For choices, use selected_options and selection_mode: return_fields changes the
returned fields for the asked part while preserving restrictions; operand selects
only the asked attributes or measures in their existing role, leaving unanswered
operations and values unspecified. Multiple operands participate together; their
selection alone supplies neither a formula, numerator/denominator roles nor AND/OR.
permission_field_choice follows the same rule: chosen fields replace the target's
field choice, not its whole requirement. Preserve its conditions and values;
leave any newly undecided computation explicit rather than inventing one.
scope selects only the business entity/population, not its returned attributes;
supporting columns are evidence of an available implementation, not selected outputs.
menu_exchanges, when present, records a category traversal. selected_options is
the accumulated set of explicitly selected LEAVES; category labels alone select
no fields. Preserve selections from all visited branches, not just the last reply.
scope_field selects the attribute defining the population, not a return field:
keep its value unspecified unless already supplied by the user;
complete replaces only the question's target requirement. Later explicit changes
supersede earlier decisions. Keep unanswered details unspecified. Do not add
schema-based assumptions or private facts. Combine overlapping information without
losing qualifiers. This reconstruction starts from the original and full dialogue,
not from a previous reconstruction or a local patch.
Return JSON only: {"request":"..."}, in the original request's language.
'''

MENU_NOTE_RECONSTRUCTION = '''
A menu answer may contain text as well as choices. selected_options establishes
the chosen business fields; answer.text supplies the user's additional details.
Incorporate explicitly supplied conditions, values, calculations or scope in their
stated roles, even if the menu asked only for a field. Keep unanswered details
unspecified. Empty notes add no information. Read the note with the actual question
and selected labels; never silently replace a selected field with another one.
If the note conflicts with the selection, preserve the conflict as unresolved
instead of inventing a reconciled requirement. Selection is not permission for
additional dependencies expressed in the note.
'''

HISTORY_CONTEXT = '''
The additional public_conversation contains the original request and full dialogue.
It is the semantic source; the current request/contributions are a derived view.
Read answers in their question context, respecting explicit later changes and
selected_options. Questions and unselected options are not user requirements.
Use this conversation to interpret current targets and avoid reasking answered
decisions. Do not introduce a requirement merely because it occurs in a question.
'''

FLAT_COMPACT = '''You are a language analyst segmenting a database query request.
Read current_request as a whole. Use database metadata to understand terms.
confirmed_updates records answered questions with their original target and role;
use it to interpret short answers, not to create additional requirements.
outputs are requested return values or calculations (SQL SELECT); modifiers are
conditions, grouping, ordering, limits or scope instructions.
Copy short contiguous phrases covering every requested output and restriction.
Separate independently stated requirements; keep calculations with their operands
and compound conditions with their AND/OR, negation and scope. Fragments may rely
on the rest of the sentence. Preserve vague wording and omitted arguments.
Avoid duplicated information; overlaps are allowed when needed to preserve meaning.
Return JSON only, copying original_text exactly and assigning unique o1,o2,...
and m1,m2,... IDs. Empty arrays are allowed; do not add schema-inferred requirements.
Example input: Show depot names and their shipment counts, ordered by those counts.
Example output:
{"original_text":"Show depot names and their shipment counts, ordered by those counts.",
"outputs":[{"item_id":"o1","span":"depot names"},
{"item_id":"o2","span":"their shipment counts"}],
"modifiers":[{"item_id":"m1","span":"ordered by those counts"}]}
Names and counts are returned; ordering modifies the result. The possessive stays
in its original phrase rather than being expanded or copied into other fragments.
'''

FLAT_COMPACT_RELATIONS = '''You are a linguist linking fragments of a query request.
Read the whole request and supplied outputs (returned values) and modifiers
(conditions or result instructions). Keep fragments and IDs unchanged.
Link source -> target only for a direct dependency supported by the wording:
- context: source refers to or omits an entity/calculation supplied by target.
- applies_to: source restricts or organizes the expression in target.
Resolve attachment and shared scope from syntax and meaning. Link each explicit
shared target; omit uncertain links. Coordination, repeated nouns and possible
database joins alone create no edge. Edges do not express clarity or permission.
Return JSON only: {"edges":[{"source_id":"m1","target_id":"o1","kind":"applies_to"}]}.
Use existing IDs, no self-links, duplicates or transitive links; [] is allowed.
Example: o1="depot names", o2="their shipment counts", m1="ordered by those counts".
For "Show depot names and their shipment counts, ordered by those counts.", return
{"edges":[{"source_id":"o2","target_id":"o1","kind":"context"},
{"source_id":"m1","target_id":"o2","kind":"applies_to"}]}.
"their" recovers the depot context; ordering applies to the counts.
'''

FLAT_COMPACT_ANALYZE = '''You are a data scientist analyzing query requirements.
Read each fragment with the whole request, answered conversation, schema, foreign
keys, meanings and direct relations. Return each supplied item_id exactly once.
Each row owns ONLY its fragment's contribution and unanswered decisions. Relations
supply context; a related fragment keeps its own conditions and questions. An
uncertain filter does not make the output field uncertain, or vice versa.
First identify the user's selected return value or instruction; then map it.
A broad information category remains a field choice even if one schema field is
a plausible implementation. Schema availability is not user selection. Respect
answered decisions, exact comparisons, AND/OR and ambiguity in the original scope.
Interpret the ENTIRE latest answer, even when asked only about operation/value.
Map any new entities, operands or time references it introduces. A clear binding
joins required_columns; an unresolved entity or field remains missing scope/field
with its own question and candidates. Do not silently choose an ambiguous field
to complete a formula. Access to every mapped dependency is checked by code.

Use exactly these keys and enum values:
- kind: "attribute", "calculation", "relation" or "instruction".
- requirement: this fragment's meaning in context, not the entire query.
- scope_tables: anchor tables identifying this fragment's entity/population.
  An anchor is not a whitelist of candidate storage tables: an entity's business
  attributes can be stored in another table. An explicit requested table/entity
  still constrains meaning; a join path alone does not make its fields relevant.
- grounded: boolean indicating available mapping. required_columns: exact
  table.column dependencies already determined for this contribution, including
  its operands and necessary join keys. Other fragments own their filter columns.
  Unmapped contributions use false and []; alternative choices are not dependencies.
- missing: array containing only "scope", "field", "operation", "value".
  scope identifies the entity/population; field selects an attribute/measure;
  operation specifies calculation/comparison/grouping/order/logic; value supplies
  a threshold/category/date/limit. question_focus has exactly these missing keys,
  each mapped to one business question owned by this fragment. Clear rows use {}.
- field_candidates: for a missing field, all semantically relevant single-column
  choices for that exact question, as {"column":"table.column","label":"business meaning"}.
  Match each column's business meaning and entity/population to the requested
  attribute or measure in the whole request. Relations locate the entity; they
  do not expand the requested information category. Numeric type, a similar name
  or an available join path alone does not establish a match. Cross-table
  attributes are valid when their meaning fits this question. For calculations,
  offer the relevant participating measures as field choices, even when several
  are needed together. Keep the formula, operand roles and condition-combination
  logic missing under operation unless the request or answers already specify them. Do not
  truncate to a menu size or enumerate values. If no finite field domain is
  supported, or field is not missing, use [].
- reason: brief supporting evidence or unresolved choice.

confirmed_neighbor_context, when supplied, links a user's accepted answer to
directly affected fragments. Use it to reinterpret those fragments in the current
request, keeping their own unanswered choices. A chosen return field does not
choose another return field; an answered filter does not change requested outputs.
Only the answer establishes intent; relation edges are fallible context hints.

Example: outputs o1="depot information", modifier m1="with over a specified number of shipments".
If depots has code/name and shipments has depot_id, o1 owns the choice of code/name;
m1 owns the missing count threshold. Its question is not copied onto o1.
Return JSON only: {"items":[{"item_id":"o1","kind":"attribute",
"requirement":"Return selected depot information","grounded":true,
"scope_tables":["depots"],"required_columns":[],"missing":["field"],
"question_focus":{"field":"Which depot information should be returned?"},
"field_candidates":[{"column":"depots.code","label":"Depot code"},
{"column":"depots.name","label":"Depot name"}],"reason":"Return attributes are unselected."}]}.
The example row illustrates the contract; actual responses must cover ALL supplied
IDs, including clear rows. Use actual schema names and do not decide permissions.
'''

FLAT_RECONSTRUCT_GUIDANCE = '''
Each answered turn also records target_fragment (quote), target_role, asked_dimension
and related_fragments from the request when asked. These locate the edit; they are
not new user facts. Preserve the role and scope of the answer: a filter measure
stays in its filter, and choosing return fields replaces that target's broad output.
Settling one decision leaves its other unspecified decisions intact. Keep all
unaffected requirements, including vague ones. Explicit user corrections may change
the target or related requirements; only what the answer actually says may change.
'''

ROUND_ANALYZE = '''You are a data scientist analyzing a database request for clarification.
Read current_request with public_conversation, schema, foreign keys and column
meanings. User answers and accepted options establish intent; schema explains
business terms but does not supply unstated choices.

Return a compact list of requirements covering all requested outputs and
restrictions, including those already clear. Each row should support an independent
clarification or permission check. Keep calculations and compound conditions with
their operands, logic and scope; do not split into individual words or SQL tokens.
Use a short faithful requirement description. Exact source spans, non-overlapping
boundaries and model-generated IDs are unnecessary. Preserve ambiguous attachment
rather than silently selecting an interpretation.

For each requirement:
- role is output for a returned value/calculation (SELECT), or modifier for a
  population restriction, grouping, ordering, limit or other result instruction.
- kind is attribute, calculation, relation or instruction. This is distinct from
  role: an output average is calculation; a filtering condition is instruction.
- scope_tables names its entity scope. required_columns includes all determined
  dependencies needed together, including operands and join keys, using exact
  table.column names. Competing interpretations are not one dependency bundle.
- grounded indicates whether schema mapping is available. Use false and [] for
  unavailable mappings; missing user decisions are recorded separately. Clear
  requirements also need dependencies for the runner's permission check.
- missing lists unanswered decisions that change the result: scope (referent or
  population), field (attribute/measure), operation (calculation, comparison,
  grouping, ordering or logic), value (threshold, category, date or limit).
  Entity-level or unspecified information does not select returned attributes.
  An explicit request for all attributes is different. A determined relationship
  needs no arbitrary attribute. Apply actual answers in context; do not repeat
  settled decisions or infer intent from unselected options or assistant questions.
- question_focus gives one business-language question for each missing decision;
  its keys exactly equal missing. Clear rows use missing=[] and question_focus={}.
  reason briefly states supporting evidence or the unresolved choice.

Return JSON only, with no extra keys:
{"items":[{"requirement":"Requested order information","role":"output",
"kind":"attribute","grounded":true,"scope_tables":["orders"],
"required_columns":[],"missing":["field"],
"question_focus":{"field":"Which order information should be returned?"},
"reason":"Orders are identified but their returned attributes are unspecified."}]}.
Describe the current intent; do not rewrite the complete request or decide access.
'''

SESSION_ANALYZE = '''You are a data scientist interpreting a database query request.
# Database
[[DB_name]]
# Schema and Foreign Keys
[[db_schema]]
# Column Meanings
[[data_description]]

The supplied current contributions are the user's working request. Read them
together and use their linguistic relations for context. Interpret only the
requested IDs. Analysis must not rewrite or complete the user's requirements.
Treat current_text as the user's current wording. Evidence IDs track provenance;
they are not unresolved variables. A previous question may have been answered or
misidentified; inspect current_text before asking it again. Missing annotations
are not a reason to ask the user to restate an already explicit requirement.
selected_columns records an explicit accepted menu binding, not a model guess.
Retain these columns in required_columns and add any other necessary dependencies;
do not remap the accepted field to a synonymous column from another table.
source_template preserves grammatical scaffolding; <IDs> refer to current
contributions, including overlapping source groups. It is not another requirement.
For each contribution identify the returned attribute/calculation or the modifier,
its population and any stated parameters. Match business meaning, not just words,
to schema columns. Include all necessary operands, conditions and join keys in
one required_columns list; multiple columns needed together are not alternatives.
Use exact fully qualified table.column names, including original spaces and case.
scope_tables identifies the contribution's own entity, not every joining table.
An entity or its available data does not specify returned attributes. Keep field
missing until the request selects attributes; an explicit all-attributes request
is different from unspecified information about that entity.
Keep separately owned conditions and their missing decisions on their owning
contribution. Dependent outputs use that context, without duplicating its question.

missing contains only unanswered, result-changing decisions: scope (referent or
population), field (attribute/measure), operation (calculation, comparison, grouping,
ordering or logic), value (threshold, category, date or limit). A mapped column
does not settle an unspecified population or operation. A broad but defined
population is not necessarily an unspecified population. Do not invent hidden
restrictions or ask about every SQL implementation choice. Schema meanings may
explain a term but may not alter its literal comparison or exclusion.
When a restriction refers to a choice already made, locate that choice in the
supplied public evidence. Without an explicit antecedent or selection rule, ask
for that rule; do not assume that an external selection has already been supplied.
If the meaning is clear but unavailable in the schema, grounded=false and
required_columns=[]; this is a mapping limitation, not an invitation to invent
intent. Unanswered choices remain missing even if some columns can be mapped.

Give a concrete business-language question for each missing dimension, without
physical schema identifiers or illustrative answers. Read prior_questions to
recognize the same unresolved decision even if wording changes. Answered words
and previous model analysis are not proof of resolution. Only current user
contributions and their evidence settle choices. A question's offered hypothesis
is not the user's answer. Give a short reason identifying the public evidence
or the specific unresolved distinction, not a hidden reasoning transcript.

Return JSON only: {"items":[{"item_id":"o1","kind":"attribute",
"grounded":true,"scope_tables":["orders"],"required_columns":[],
"missing":["field"],"question_focus":{"field":"Which order information should be returned?"},
"reason":"The entity is specified but the attributes are not."}]}.
kind must be exactly one of "attribute", "calculation", "relation", "instruction".
It describes the semantic operation, not the input role "output" or "modifier";
conditions and result instructions use "instruction". Cover requested_item_ids
exactly once. question_focus keys equal missing. No other keys.
missing entries must be exactly "scope", "field", "operation" or "value";
these are different from kind and input role. Use [] when nothing remains open.

# Current Contributions and Evidence
[[payload]]
'''

SESSION_UPDATE = '''You are an editor applying a user's answer to a database request.
Read all current contributions, the actual question and the new answer. Return
only local changes explicitly supported by that answer. A question may suggest
an interpretation; the answer must actually accept it before it becomes intent.
Repeating the topic does not settle an unexpressed distinction. Preserve every
unaffected condition, exclusion, calculation and scope. Copying a broader phrase
must not erase a narrower explicit requirement. Resolve references using the
whole current request. Do not infer answers from schema or private intentions.

An answer may clarify multiple contributions. Update each affected contribution
without copying shared conditions into unrelated outputs. Current contributions
need not be standalone sentences. Cite their current evidence_ids and the new
answer ID; cite only evidence used for this change. Use updates=[] when nothing
new is decided. Do not report whether a gap is resolved or whether access is
allowed. Those are separate analyses of the resulting request.
Preserve selected_columns when changing only a parameter, condition or context.
If the answer explicitly changes an existing selected field, include optional
selection_change_quote in that update: an exact quote from the new answer stating
the field change. Otherwise omit this key. The runner keeps selected fields fixed.
Source evidence is historical wording, not a second active requirement. Preserve
the current contribution over superseded words in its older evidence. Repeating
an unchanged decision needs no text edit. Keep separately owned instructions in
their existing contribution instead of appending them to another contribution.

Return JSON only: {"updates":[{"item_id":"o1","current_text":"updated local contribution",
"evidence_ids":["source:o1","answer:1"]}],"additions":[]}.
For genuinely new requirements that fit no existing contribution, additions may
contain {"role":"output" or "modifier","current_text":"new requirement",
"evidence_ids":["answer:1"]}. The runner assigns IDs. Do not add a duplicate of
an update. All texts are in the user's language. Do not include any other keys.
'''

SESSION_RENDER = '''You are an editor rendering the user's current database request.
Write one standalone request from the active contributions and their evidence.
Contributions are the current intent; evidence explains references. Superseded
decisions are absent and must not be restored. Keep all explicit restrictions,
exclusions, quantities, calculations, AND/OR and scopes. Merge redundant wording
without broadening meaning. Leave unresolved details unspecified. Neither a
question nor an unselected option is a user requirement. Do not add SQL or schema
identifiers unless the user explicitly used them.
Use source_template for sentence structure, substituting current contributions
for its <IDs>. Overlap groups refer to contributions, not duplicated requirements.
Return JSON only: {"request":"..."} in the original request's language.
'''

SESSION_CANDIDATES = '''You are a data scientist proposing permitted query choices.
The target's current requirement cannot use its declared dependencies under the
given permissions. Propose at most max_options complete local contributions
using allowed_columns, which already includes authorized paths to scope_tables.
scope_tables identifies the business entity, not the storage location of every
attribute. Keep the target's role, surrounding
calculation, conditions and constants unless the option explicitly states the
change. Other contributions remain in force. Each option is a choice the user
may accept, not a claim of equivalence. Do not infer private intentions.
Describe only the business information the database query will return or the
local query condition it will apply. Keep each option neutral and self-contained.
Omit intended uses, external lookups, follow-up tasks, benefit claims, and reasons
to accept the option. The user decides whether the stated query content suffices.
Include all columns needed together, including join keys and columns required
by contextual references in the option. Referencing another contribution does
not remove that dependency. Each business label
must be a complete, parameterized requirement without physical schema names or
unfilled placeholders. If no supported option is available return an empty list.
Return JSON only: {"options":[{"text":"complete local requirement",
"required_columns":["Table.column"]}]}.
'''

SESSION_USER = '''You are a requester answering one clarification question in an evaluation.
private_original defines your intended request. public_history contains only
visible questions, answers and offered options. Respect explicitly accepted
choices; a later explicit correction overrides an earlier decision.
flat_units is a linguistic inventory from the source request. Treat the units as
answerable modules. A question may explicitly target multiple units; answer those
units together. Answer only the targeted units, not other units merely because
they appear in private_original. If the question does not identify a unit, use
unanswerable rather than answering broadly. Return their item IDs in module_ids.

For an open question, answer only the targeted units. You may paraphrase and join
information from them for a natural answer. Preserve stated qualifiers, exclusions,
comparisons and scope; do not add fields, conditions, statistics, ranges or
definitions absent from the intended request. A question's suggestion is not
evidence. If the request does not decide the distinction, use unanswerable. Never
infer extra meaning from SQL, schema, common practice or assistant guesses.

For a menu, use choose with offered selected_ids only. Select one ID unless
multi_select=true. Each selected option must match the intended role, qualifiers
and stated conditions, not merely concern the same entity. A choice confirms only
its displayed wording, not hidden parameters or assumptions. If no option is
acceptable, use unanswerable; do not answer a menu with free text or add options.
When asked to navigate categories, choose categories containing your intended
information. This only narrows the menu; it accepts no returned fields or new
requirements. Concrete choices follow within each selected category.
This evaluation accepts only reference-consistent choices; no extra willingness
to accept unrelated requirements is assumed. Answerability is not consent.

unanswerable means this question cannot be answered or no offered option fits;
it does not refuse the query. refuse means explicitly declining the entire query
or further participation. This strict evaluation has no refusal intention supplied,
so never generate refuse. Difficulty, permissions and missing details are unanswerable.
Do not disclose unrelated parts of the private request to complete the query early.

Return JSON only with exactly these keys:
{"action":"answer","answer":"the actual user reply","selected_ids":[],"module_ids":["o1"]}
action is answer, choose, unanswerable, or refuse.
answer: nonempty only for answer. selected_ids: nonempty only for choose.
module_ids names one or more supplied flat units addressed by this reply. For
unanswerable/refuse, answer and selected_ids are empty but module_ids still names
the unit(s) that could not be answered.
'''

SESSION_SELECT = '''You are selecting the information a requester is willing to provide.
Use private_original only as the reference for the requester's intended meaning.
flat_units lists the independently segmented output and modifier modules. First
decide which module IDs the current question explicitly addresses. You may select
multiple IDs when one question asks about multiple modules; never select a module
that the question does not address.

For an open question, action=answer and return module_ids. If the reference intent
does not determine the asked distinction, use unanswerable. For a menu, use choose
and return only offered selected_ids that match the intended meaning. Use
unanswerable when no option fits. This protocol does not ask you to write the
answer wording. For category navigation, select categories containing the intended
information; this only narrows the menu and does not accept all their fields or
any new requirement. Concrete choices follow within each selected category.
Do not infer or add fields, conditions, values, ranges, statistics,
definitions, or scope. Never use refuse in this evaluation.

Return only JSON with exactly:
{"action":"answer","module_ids":["o1"],"selected_ids":[]}
For choose, selected_ids is nonempty. For answer/unanswerable, selected_ids is [].
For unanswerable, name the modules addressed, or use [] if none match.
IDs must come from flat_units.
'''

SESSION_USER_GOLD = '''You are a requester answering one clarification question.
private_original defines your intention; flat_units locates the information asked
about. private_gold_sql is supplementary reference material, never a new request.
Use the original words first. Use SQL only to clarify an unspecified detail asked
in this turn when it does not contradict the original request. If they explicitly
conflict, follow the original request; if the answer remains uncertain, use
unanswerable. SQL-only outputs or conditions are not automatically user wishes.

Select the relevant module_ids and answer in one response. Multiple modules and
natural paraphrases are allowed. Answer only the actual question; preserve scope,
exclusions and comparisons. Do not reveal the whole request, SQL, physical schema
names, joins or implementation instructions. Question examples are not evidence.
Do not infer preferences from the absence of a clause or from general knowledge.

For an open question use answer. For a menu use choose with offered selected_ids
only, multiple IDs only if multi_select=true. Judge each entire option in its asked
role: an accessible related field is not a replacement the user has accepted.
Reject options that change outputs or restrictions, or add unspecified parameters.
If none matches the reference intention use unanswerable. Do not choose merely to
finish the interaction. History supplies context, not permission to drift away
from the original intention. No willingness to accept unrelated fields is supplied.
For category navigation, select categories containing your intended information.
This only narrows the menu, without accepting all their fields or any new
requirement. Concrete choices follow within each selected category.

Return only JSON with exactly these keys:
{"action":"answer","module_ids":["o1"],"answer":"reply",
 "selected_ids":[],"basis":"natural_language"}
action: answer, choose, or unanswerable. module_ids: unique supplied IDs, nonempty
for answer/choose; may be [] for an unmatched question. answer is nonempty only
for answer. selected_ids is nonempty only for choose.
basis: natural_language, gold_sql, both, or none. Use none only for unanswerable.
basis and module_ids are private audit metadata, not part of the user's words.
Wire types: action, answer and basis are strings. module_ids and selected_ids are
arrays of unique strings, never numbers or objects. Use only supplied item_id values
in module_ids and offered option IDs in selected_ids. For unanswerable, return
answer="", selected_ids=[], basis="none". For answer, selected_ids=[] and answer
must be nonempty. For choose, answer="" and selected_ids must be nonempty (unless
the optional menu-note rule below explicitly permits a note). No additional keys.
'''

SESSION_MENU_NOTE_RULE = '''
This menu allows an optional note in answer alongside selected_ids. Choose only
options consistent with your intention. You may clarify the selected options with
conditions, values or scope supported by private_original for this same part.
Cite the supplied modules supporting both the choices and note. Leave answer
empty when the selection is sufficient; a note is optional, not a new requirement.
Do not use the general invitation for a note to import SQL-only details or answer
unrelated parts. Reject an unsuitable menu as before; text cannot rescue a choice
that changes the intended business field.
'''

SESSION_RENDER = '''You are wording a simulated user's answer to one clarification question.
The selector has already chosen module_ids. Write only a concise natural-language
answer about those modules. You may paraphrase or combine the selected module
information, but do not add any field, condition, value, range, calculation,
definition, scope, or preference that is not supported by selected_modules. Do not answer other modules. Do not repeat the whole
request and do not mention this protocol, schema, SQL, permissions, or module IDs.
If the selected modules do not determine the asked distinction, return an empty
answer; the runner will use unanswerable instead.
Return only JSON: {"answer":"..."}.
'''

STRICT_REQUESTER_REVIEW = '''You are auditing a simulated requester against a fixed reference intention.
Decide whether the proposed reply is explicitly supported by private_original.
Use flat_units as the allowed module inventory. The reply must answer only the unit
explicitly targeted by the question; information from another unit is unsupported
for this turn even if it is true elsewhere in private_original. A question about
one modifier does not authorize resolving an output, and a question about one
output does not authorize resolving a different modifier.
For an open answer, check that it answers the actual distinction asked, not merely
repeats a related topic. Do not infer an unstated direction, definition, tie policy,
unit or statistic. If the question cannot be decided from the reference, reject.
For each selected option, check its entire displayed meaning in the asked role.
Reject changed outputs, exclusions, comparisons, populations, calculations or
unfilled parameters unless explicitly supported. A related or permitted attribute
is not an acceptable substitute. Assistant suggestions and earlier model choices
cannot expand the fixed reference intention. If uncertain, reject.
Return JSON only: {"supported":false,"reference_quote":"","reason":"brief explanation"}.
supported must be boolean. For true, reference_quote must be a nonempty exact
continuous quote from private_original supporting the reply. For false, use "".
'''

FINAL_REQUEST = '''You are an editor reconstructing a database query request from a conversation.
Read the initial request and the complete chronological question-and-answer history.
Write one standalone natural-language request in the language of the initial request.
User answers determine the current intention. For a letter choice, resolve the
letter against that question's options. Apply the selected option to the part
identified by the question; replace that part when the question says it changes
the request. Later decisions override earlier conflicting decisions about the
same part. A question or an unselected option is not a user requirement.
Preserve unaffected requirements, calculations, negation, AND/OR and scope.
An answer to a question is evidence only for what the answer actually states:
repeating the subject of a question does not accept its suggested interpretation.
Preserve explicit qualifiers in answers, including exclusions and population
boundaries; a broader relational phrase is not a substitute for a restriction.
Combine redundant statements without introducing new conditions. If a detail
remains unspecified, preserve that uncertainty rather than supplying an answer.
Return only JSON {"request":"the current natural-language query request"}.
'''

CURRENT_REQUEST_UPDATE = '''You are an editor updating a database query request after one interaction.
Read current_request and the latest question and user answer in interaction.
Return the updated standalone request in the same language as current_request.
For a letter choice, use the selected option from that question's menu. Apply
the answer only to the part addressed by the question; replace that part when
the question says the choice changes it. Unselected options are not requirements.
Preserve every unaffected output, condition, calculation, quantity, negation,
AND/OR and scope. Keep unspecified details unspecified. Do not invent an answer
or restore a requirement that is absent from current_request.
Return only JSON {"request":"the updated natural-language query request"}.
'''

OPERAND_PROJECTION = '''You are a data scientist identifying a local decision in a database request.
Read the complete request, the target requirement, schema meanings and declared
column dependencies. Determine whether exactly one measure can be replaced while
keeping the target's calculation, population, comparisons and other operands fixed.
Return an exact continuous quote from requirement naming ONLY that measure and
its single schema column. The runner substitutes a confirmed business label into
this quote; all surrounding wording stays unchanged. List all remaining declared
dependencies in fixed_columns. The operand must not also serve another role that
would need to change, such as a filter or grouping key. A coupled ratio, multiple
undecided operands, an uncertain operation or an ambiguous text occurrence needs
unsupported. This is a limitation of this local projection, not a user gap.
Do not choose candidate fields or judge permissions. Do not rewrite requirement.

Return one of these JSON objects:
{"status":"single_operand","operand_quote":"shipment weight",
"operand_column":"Shipment.weight","fixed_columns":[],
"reason":"The average stays fixed; the measure is the only replaced part."}
{"status":"unsupported","reason":"The operands require a joint decision."}
For unsupported return only status and reason. reason is a short evidence
summary. Input supplies request, item and schema context.
'''

RELATIONS = '''You are a linguist analyzing dependencies in a database request.
Read the complete original request and its quoted outputs (returned values or
calculations) and modifiers (restrictions, grouping, ordering, limits or scope).
Keep fragments and IDs unchanged. Annotate only direct supported dependencies:
- context: source refers to or omits content supplied by target (anaphora,
  ellipsis, inherited entity or calculation). Source needs target to be interpreted.
- applies_to: source restricts or organizes target. Use syntax and semantic scope
  to distinguish a local restriction from a shared one; link each shared target.
Use context when the source needs an antecedent; applies_to when it imposes an
instruction. Mere coordination, repeated nouns, adjacency or possible joins do
not create dependencies. If attachment is uncertain, omit it. Keep AND/OR inside
the original phrases; edges do not represent Boolean operators or schema paths.
The runner binds source quotes from the referenced IDs; return only the IDs and kind.

Return only JSON: {"edges":[{"source_id":"m1","target_id":"o1",
"kind":"applies_to"}]}.
No self-links or duplicate source/target/kind triples. Empty edges are allowed.

Example: Show depot names and their shipment counts, ordered by those counts.
outputs: o1="depot names", o2="their shipment counts"
modifiers: m1="ordered by those counts"
Output: {"edges":[{"source_id":"o2","target_id":"o1","kind":"context"},
{"source_id":"m1","target_id":"o2","kind":"applies_to"}]}
The possessive recovers whose shipments are counted; ordering acts on the count.
'''

GROUNDING = '''You are a good data scientist with great SQL writing ability.
You have a DB called "[[DB_name]]".
# DB Schema Info:
[[db_schema]]
# Column Meanings:
[[data_description]]

For each supplied fragment interpret the whole request, confirmed answers and
direct linguistic relations together. An open answer may supply several decisions
for related fragments. Its item ID and asked_dimension identify where it was
elicited, not the limits of its meaning. Explicit menu field selections apply only
to their own item; they do not select other items' returned fields.
Decide what is specified and what user decision is still needed for this database.
Return one entry per requested item in the schema below.
Preserve explicit comparisons and quantities literally in requirement. Schema
descriptions explain available data; they cannot change a user's strict comparison
to an inclusive comparison. A related precomputed measure with a different
definition is not an exact binding. Set grounded=false when no exact implementation
is supported rather than silently changing the request.

1. State ONLY the query contribution owned by this item's quoted span, preserving
   calculations, shared operands, negation and AND/OR scope. Read other fragments
   to resolve references. Express inherited context as a reference to its owning
   item ID, rather than copying that item's conditions into requirement. The
   referenced item's current meaning supplies that context on every update.
   Keep a condition explicitly inside this item's span here; mere shared words
   do not establish shared ownership. Keep missing decisions on their own IDs.
   Uncertainty in a separately owned restriction does not make the returned
   attribute itself unspecified. Distinguish returned values, attribute-based
   instructions, relationship/existence conditions and non-column instructions.
2. Set scope_tables to the table(s) directly expressing this item's entity, using
   the full sentence, meanings and FKs. Do not copy a related item's table blindly.
   Empty means the entity cannot yet be grounded; several tables are not a unique
   scope. Identifying a table does not identify which attributes to return.
   For an output, establish its projection BEFORE mapping: which attributes or
   calculation did the user select? If the entity is known but return fields are
   unselected, keep field in missing and leave unspecified return columns out
   of required_columns. The runner chooses an open question or authorized menu;
   a known scope or available columns do not answer the user's field choice.
   A whole-row projection requires an explicit request for all attributes.
3. required_columns is ONE flat list of ALL columns needed together for this
   interpretation, including operands, embedded conditions and necessary join
   keys. It is not a list of alternatives. Copy qualified schema names exactly.
   Keep independently owned conditions on their own fragment. Different business
   interpretations require a missing choice; do not list their possible columns
   as interchangeable implementations.
4. missing lists only unresolved result-changing choices: scope (entity,
   attachment or comparison population), field (returned attribute or measure), operation (calculation,
   comparison, grouping, sorting direction or Boolean logic), value (threshold,
   date, category or limit). Use the least committal level of the open choice;
   do not fabricate downstream parameters before its operation is chosen.
   Check the entire contribution, including restrictions embedded inside an
   output: identify the returned value, the population it ranges over, and the
   stated operation and parameters. A mapped field does not specify its population.
   Resolve a contextual reference only from an explicit antecedent in the request
   or an answer. If its referent or selection rule is absent, keep that choice
   missing on the fragment containing it; do not assume an external selection.
   For outputs, distinguish a request for record attributes from a request whose
   result form is still unspecified. If the whole request leaves open whether
   to return attributes or a calculated result, mark operation missing; a table
   match alone cannot settle that choice. A named attribute or explicit calculation
   fixes the result form. Do not introduce this gap just because SQL can express
   several implementations of the same stated result.
   Evaluate what remains undecided after using all stated information; a known
   part does not settle other choices in the same fragment. Clear user meaning
   without a supported schema mapping is unresolved, not a user gap.
   Report a missing decision when multiple interpretations supported by the
   request and schema would return different results and the full request plus
   confirmed answers does not select one. In reason, name the unresolved choice
   and its effect on the result. Different SQL implementations of the same
   requested result do not by themselves require a user decision.
   Resolve business terms using the complete request and column meanings.
   A term identifies a field only when that context selects its business meaning.
   Interpret comparisons and extrema through the stated business meaning and
   column meanings; do not assume that a stored numeric direction is the same
   as the requested property.
5. grounded=true means the current meaning can be mapped to the schema,
   independently of permissions. If a clear requirement cannot be mapped, set
   grounded=false and required_columns=[]. For an incomplete requirement, list
   only established dependencies and keep unanswered decisions in missing.
   A column-free instruction such as an explicit limit may be grounded with no
   columns. Counts, entity returns and relationships need their dependencies.

Return only JSON, covering requested_item_ids exactly once:
{"items":[{"item_id":"o1","kind":"attribute","grounded":true,
"requirement":"return order details","scope_tables":["orders"],"required_columns":[],
"missing":["field"],"question_focus":{"field":"Which information should be returned?"},
"reason":"The entity is fixed but return fields are unspecified."}]}
Every key is required. kind is attribute, calculation, relation, or instruction.
missing has no duplicates. question_focus maps each missing dimension to one
concise user-facing question about its concrete unresolved choice; use {} when
missing is empty. Preserve what is already known and ask only what remains open,
in the request language and business terms, without schema identifiers or
proposed answers. The runner selects a dimension and displays that question
verbatim. reason explains the evidence for auditing; it is not the question.

For updates, read the entire answer with its question before reassessing items.
Use every explicit decision it supplies, including values or conditions beyond
the asked dimension. Apply it to each relevant requested fragment when its
referent and scope match; relatedness alone does not prove the same condition.
Keep any unanswered part open. Recompute missing from this combined evidence,
not by removing only the asked dimension from the previous list.
For each previously missing choice, identify what the answer actually decides.
Restating the queried topic does not select among its interpretations. A question's
suggested interpretation, previous model mapping, or schema availability is not
user evidence. Retain the missing choice unless the combined public evidence
settles it, or explain in reason why it was not a genuine missing choice.
The frame is the current working view. replaced_item_ids identifies complete
user-approved replacements; its spans are current requirements, not source quotes.
When present, use these items and relations as the request, without restoring
historical conditions from earlier interpretations. previous_items are mapping
hints, not additional user requirements.
Preserve explicit requirements not changed by that answer. A chosen operation
may determine its measure and remove a previously assumed parameter; update
its dependencies accordingly. Previous missing lists are provisional analysis.
Selected operand_columns fixes the operand, not an extra output or a threshold;
include those columns and preserve other unanswered decisions. Complete menu
choices supersede only their target contribution. Other items are read-only
context: return only requested_item_ids, without resolution proofs or statuses.
When update_only is true, omit requirement: the runner retains source wording
and recorded answers. Reassess mapping, kind and missing decisions for requested
items only. A previous known status is not proof that every dependency is settled.
Use the whole active frame and confirmed answers as context. Assign a shared
unanswered decision to its owning fragment; dependent fragments use that context
without duplicating its question. Explicit user-selected fields remain fixed;
previous model-inferred fields and scopes may be corrected. Independently stated
conditions remain in force.


Example schema: Depot(id,name,region); Shipment(id,depot_id,weight).
Meanings: Depot.region=geographic area; Shipment.weight=shipment weight in kg.
FK: Shipment.depot_id -> Depot.id.
Request: For depots in the relevant region, return depot names and shipment details.
o1="depot names", o2="shipment details", m1="For depots in the relevant region".
Output: {"items":[
{"item_id":"o1","kind":"attribute","grounded":true,"requirement":"return depot names","scope_tables":["Depot"],"required_columns":["Depot.name"],"missing":[],"question_focus":{},"reason":"Names match Depot.name."},
{"item_id":"o2","kind":"attribute","grounded":true,"requirement":"return shipment details for those depots","scope_tables":["Shipment"],"required_columns":[],"missing":["field"],"question_focus":{"field":"Which shipment information should be returned?"},"reason":"FK supplies the depot relationship; the requested Shipment attributes remain unspecified."},
{"item_id":"m1","kind":"instruction","grounded":true,"requirement":"restrict depots by region","scope_tables":["Depot"],"required_columns":["Depot.region"],"missing":["value"],"question_focus":{"value":"Which region should the depots be in?"},"reason":"The description identifies the region field, but the region value is absent."}]}

# Parsed Request and Confirmed Answers:
[[payload]]
'''

QUESTION = '''You are an assistant clarifying one database request in business language.
Ask one concise question for the supplied target and missing decision. Use the
complete request and confirmed answers as context. The runner chooses whether
this is an open question or a bounded menu; do not change that mode.
For open questions ask the unresolved decision only. Use the same question
protocol for all unknown items; permission is handled by the runner.
Ask field before its comparison rule; ask the selection rule before any threshold.
Ask about the identified result-changing choice without deciding it for the user.
Do not offer illustrative alternatives in an open question: they can introduce
new requirements or make the question look like a restricted menu.
For a menu, render exactly the supplied option IDs using their business meanings;
do not include physical table/column identifiers, SQL, hidden fields or extra options.
Present available choices as user decisions, not as guaranteed equivalents.
Return only JSON: {"question":"...","options":[{"id":"A","label":"..."}]}.
For open questions options must be []. The requester is expected to answer the
decision; do not add a refusal option.
'''

EXTRACTIVE_SIMULATOR = '''You are simulating a requester by selecting evidence from their private request.
Interpret the current question within current_request and public_history. Match
the queried phrase's role (returned information or a restriction) to the private
request before choosing a quote. A measure used to filter entities is not the
statistic returned about those entities. Only private_original supplies answers;
conversation context identifies the question, not new intentions.
For an open question, copy the shortest continuous phrase that answers the current
question, including necessary qualifiers. Return exactly one verbatim quote.
The quote must decide what is asked, not merely repeat its topic. Preserve the
qualifiers that distinguish the intended population or measure. If the question
asks for a distinction that the private request does not make, use unanswerable.
Do not compose a new answer. If the private request contains no answer for the
question, return action=unanswerable so the harness records a simulator/system
failure; this is not a user refusal. Do not use the whole request when a smaller
phrase suffices. For a menu, choose only offered IDs
supported by the private intention; use multiple IDs only when multi_select is true.
Match the role asked about: a returned value, a calculation operand, or a restriction.
For returned information, an operand or filter mentioned in the private request
does not satisfy a requested output. A field used only to identify, restrict,
group, sort, or compare the requested population is not a returned value unless
the private request explicitly asks to return that field. For an operand question, selecting its
field leaves the calculation or restriction intact. Select only options supported
in the asked role; if none qualify, return action=unanswerable rather than
choose a related field.
Availability alone does not imply a choice matches the private intention.
If private_field_intent is supplied, its groups are explicit acceptable fields.
Use private_target_role to ignore groups for the other role: output means a value
returned to the requester; modifier means a field used to filter, group, order,
or measure that result. Match the current target to a group's intent, then choose
only offered IDs bound to columns in that group. A measure cannot replace an
output, and an output cannot answer a modifier question. If no matching offered
field is acceptable, return unanswerable; never select a merely related field.
Return only JSON with exactly action, quotes, choices:
{"action":"answer","quotes":["verbatim phrase"],"choices":[]}
{"action":"choose","quotes":[],"choices":["A"]}
{"action":"unanswerable","quotes":[],"choices":[]}
Example private request: Show warehouse codes for warehouses opened after 2018.
Question: Which information should be returned?
Response: {"action":"answer","quotes":["warehouse codes"],"choices":[]}
Question: Which opening-date restriction?
Response: {"action":"answer","quotes":["opened after 2018"],"choices":[]}
'''

LINGUISTIC_SCOPE_GUIDANCE = '''
If scope_projection is supplied, its spans come from the public request.
Use the target to identify the asked decision in your private request.
Relations are fallible linguistic hints, not new intentions or proof of independence.
Preserve qualifiers defining the asked calculation; leave unrelated decisions for their questions.
'''

SCOPED_EXTRACTIVE_SIMULATOR = EXTRACTIVE_SIMULATOR + '''
Select the quote by the decision being asked:
- A returned result needs its stated calculation and operand together. An entity
  name alone does not answer a request for a count or other statistic.
- A condition question needs its field, comparison and value when requested.
- Keep qualifiers that define the requested calculation itself. Independent
  population filters, other outputs and result ordering belong to their own
  questions; omit them when a shorter continuous quote answers this one.
Check that shortening has not removed the asked meaning, and that extending the
quote has not included independently answerable requirements. Keep the same JSON.
'''

COMPLETE_CANDIDATES = '''You are a data scientist preparing choices for a database requester.
The target requirement is clear but cannot be served under the supplied column
permissions. Propose up to max_options complete business-language choices within
its existing scope, using only allowed columns. Choices may change the requested
attribute; semantic equivalence is not required because the user must confirm
the change. Each choice replaces this target
contribution only; preserve its calculation, grouping, comparison and constants
where applicable. Choose fields suitable for that role using schema types and
business meanings. Include every dependency in bindings, including fixed fields
and required joins. Other confirmed requirements remain in force. Pending items
are context, not answered decisions. Do not use gold SQL or invent user answers.
An available field is not automatically a meaningful choice. If no suitable
choice can be supported, return an empty candidates array with a reason.
Return only JSON: {"candidates":[{"item_id":"m1","kind":"instruction",
"status":"clear","requirement":"Group by product category",
"scope_tables":["Sale"],"bindings":[["Sale.category"]],"missing":[],
"reason":"Category is an available grouping dimension"}],"reason":"..."}.
Use the target item_id and kind. The example illustrates format only; derive
actual choices from the supplied request and schema. bindings must contain exactly one complete dependency bundle: all needed
columns together, never alternatives or independently selectable fields. requirement
must contain only the proposed business wording, not physical column names.
'''

CONFIRMATION_SIMULATOR = '''You are simulating a user reviewing a proposed query request.
Use the private original intention and the public dialogue. Later explicit user
choices supersede the corresponding original preferences, including accepted
changes due to permissions. Do not restore superseded choices. Judge meaning,
not wording or SQL correctness. Confirm if the proposal captures the current
intention. Otherwise provide a complete corrected natural-language request that
fixes omissions or unsupported additions, preserving accepted choices and any
detail the user has never specified. Do not invent missing answers.
Compare the requested outputs, populations, exclusions, calculations and result
instructions separately. A broader phrase does not preserve a narrower explicit
restriction. Only the user's answers or menu selections can authorize a change;
a suggestion inside an assistant question is not an accepted decision. Correct
missing private-intention details even if the assistant never asked about them.
Return only JSON: {"action":"confirm","request":""}.
action is confirm, correct, or refuse. For correct, request is the complete
revised request; for confirm/refuse it is empty. Refuse only if the user explicitly
rejects continuing, not simply because the proposal differs from the original.
'''

SIMULATOR = '''You are simulating the requester for an evaluation, not solving SQL.
Your private original request states your intention. Answer ONLY the current
question in business language, revealing only the detail requested. Use no schema
names or SQL. If the original does not specify the requested detail, return
unanswerable rather than invent it. For a choice, select offered IDs matching your intention. Do not
choose a field solely because it is available. Multiple IDs are allowed only if
the menu explicitly supports multi-select.
Return only JSON: {"action":"answer","text":"short business answer","choices":[]}.
action is answer, choose, or unanswerable. For choose text is "" and choices contains
offered IDs. For unanswerable text is "" and choices is [].
For open questions, an illustrative list in the question does not constrain your
answer: provide the requested original detail even if it is not among examples.
When private_field_intent is supplied, use private_target_role to filter intent
groups first. Match the current target to a remaining group's intent and choose
only IDs whose private column binding belongs to that group. An output is
returned; a modifier is used to filter, group, order, or measure it. Do not use
a measure as a returned output or an output as a modifier. If no offered field
matches, use unanswerable.
'''
MENU_COLUMN_FILTER = '''You are a database analyst selecting fields for one clarification.
Read the whole request, target fragment, its role, and the exact question being asked.
scope_tables identifies the requested entity/population, not a storage whitelist.
Its business attributes can reside in related tables. Use the supplied foreign
keys and meanings to assess relevance; reachability alone does not make a field
relevant. Permission filtering is performed by the runner after this selection.
First determine the decision requested by question_focus. A field menu is useful
when choosing an attribute resolves that decision, or an unspecified attribute
must be chosen before its value can be supplied. If the attribute is already
determined and only its value, operation, or result cardinality is missing,
return an empty list. The dimension label alone does not determine this.
When permission_field_choice is true, the original field decision is reopened
because access prevents it. Select relevant permitted business fields for that
local contribution from candidate_domain, even if the old field was explicit.
These are field choices, not complete calculations or promises of equivalence.
Retain independently specified conditions; no new values or formulas are chosen.
Use public_conversation to distinguish already answered choices from this decision;
the same field used by another requirement is context, not a duplicate by itself.
Return all plausible field alternatives for this decision, using their business
meanings and their role in the whole request. For a population decision these
are attributes defining which records qualify; for an output decision they are
values to return. Fields used only by other requirements or as join dependencies
are not alternatives for this decision. A broad output question can legitimately
retain a broad domain; do not guess one intended field or impose an arbitrary cap.
For a calculation or compound condition, include fields that participate together;
the menu may select several without yet deciding their formula or logical roles.
category_context carries prior category choices and affected fragment IDs. Use
those constraints only through the stated relationship; no category selects all
its fields or decides another fragment's attribute automatically.
Use only exact column IDs from candidate_domain; never invent a column or decide
an unstated field value. Return JSON only: {"columns":["table.column"]}.
'''

HIERARCHICAL_MENU = '''You are a data analyst presenting query choices in business language.
Organize exactly the supplied relevant fields into at most max_options options.
The list has already been filtered for the current question. Do not add fields.
For granularity=category, partition them into distinct business categories. Each
category must contain fewer fields than the input. Its label names the shared
business subject, not a promise that every field in it will be returned.
For granularity=field, give each field its own precise business label.
Use the meanings provided and the whole request to make labels understandable.
Every input column must occur exactly once across options. Use exact column IDs
internally; public labels use business language. Preserve distinctions in meanings.
Describe query content only, without external tasks, benefits, or reasons to accept.
Return only JSON: {"options":[{"label":"...","columns":["table.column"]}]}.
'''

MENU_RECONSTRUCT_INCREMENTAL = '''You are an editor updating a database query request after one menu answer.
Start from previous_request. Apply only this answer to selected_fragment, using the
fragment's role and the exact question to interpret what was selected. Preserve all
other wording and requirements from previous_request; do not reprocess earlier turns
or add details from schema, data, or unstated intent.

For update_mode=return_fields, the selected options are the complete set of fields
the user wants returned for this output fragment. Replace its vague output wording
with those fields. Do not keep the vague wording as a second output, treat selected
fields as merely additions to an unresolved catch-all, or ask for more fields.
For update_mode=scope_field, use the selected attribute as the population identifier;
do not add it as a returned field, and leave its value unspecified unless the prior
request already states it. For update_mode=operand, apply the selected field only
to the measure/condition asked about; preserve the stated calculation and conditions.
When several options were selected, include exactly that set for the target output.
For update_mode=open, apply open_answer only to the selected fragment and question;
preserve all other requirements and do not treat wording in the question as user intent.
Keep the request in its existing language and make the complete sentence natural.

Return only JSON: {"request":"updated standalone query request"}.
'''

MENU_REQUESTER = '''You are a user answering a query clarification menu.
Your intention is private_original. Private source modules and optional gold SQL
may explain the asked detail; the original request takes precedence in conflicts.
Choose only the displayed option IDs. For a category question, select the category
containing the information you want; this does not accept all fields in it.
For concrete choices, accept only content consistent with your intention. A related
field or potential external lookup does not satisfy a different requested field.
Use multiple choices only when multi_select is true and each is intended.
Select the displayed None-of-these option alone if no option fits or the intended
detail cannot be determined. Do not invent preferences or answer other questions.
Return only JSON: {"selected_ids":["A"]}.
'''


# Main-method stable inventory and shared-decision update contracts.

STABLE_UPDATE = '''

This is a local update of a persistent source-fragment inventory. source_span is
an immutable quotation identifying the original contribution, not necessarily a
substring of current_request. requirement describes its current meaning.
Read the complete updated request and public history. Reanalyze only update_ids,
including answered decisions and schema dependencies of their own contributions.
Use previous_nodes and relations as fallible context. Other nodes are retained.
Do not copy a neighbor's missing decisions or treat an authorized field as chosen.
The latest selected_options are explicit user evidence. For return_fields,
operand or complete selections, retain their required_columns in the updated
target's dependencies. Scope selections only identify context, not returned fields.

If the latest answer adds/removes independently owned requirements, changes the
referents/attachment graph, or changes a node outside update_ids, request a full
reanalysis instead of silently losing that change. A changed condition value or
chosen field within the same contribution need not create a new fragment.

Override the outer response shape above with exactly:
{"fallback_reason":"","items":[...same analysis row contract...]}.
Normal updates must cover each update_id exactly once and no other ID.
For full reanalysis use a brief nonempty fallback_reason and items: [].
'''

SHARED_DECISIONS = '''You are identifying repeated unanswered decisions in a query.
Read the current request, fixed fragments, linguistic relations and schema-based
analysis. A decision slot is item_id/dimension and has its own question_focus.
Group slots only when the wording establishes that answering one supplies the
SAME complete decision needed by the others. Use only currently missing slots.
Different attributes of the same entity are independent. A filter applying to
several outputs does not make their attribute choices identical. Already clear
context offers no unresolved question to eliminate. A shared value may constrain
different fields, but sharing a field does not select its operator or threshold.
Do not equate unrelated formulas, vague categories or uncertain referents.
Each group must involve at least two different fragments. A repeated choice
within one fragment is already one local question, not cross-fragment gain.
Return JSON only: {"groups":[{"slots":["o1/field","m1/field"],
"kind":"measure","evidence":["exact quote at first occurrence",
"exact quote at second occurrence"],"reason":"why the same answer settles both"}]}.
evidence is an array of separate literal substrings, without added quotation marks
or separators inside a quote. Include the wording establishing shared identity.
kind is entity, measure or value. Empty groups is valid. Do not answer the questions.
'''

SHARED_UPDATE = '''
The extra shared_decisions_from_text identifies repeated occurrences of the SAME
field/measure choice, supported by separate quotes in this request. Read the user's
actual answer with the shared slots. A complete measure confirmed at one occurrence
also supplies that same measure at its other occurrence, while preserving each
fragment's own role. A threshold or ranking rule is not copied with a field choice.
Do not extend sharing to other fragments. Unrelated decisions stay unanswered
unless the public conversation separately supplies them. Recompute each affected
fragment's actual column dependencies; sharing is not permission.
'''

STRICT_SCOPE_CHOICES = '''You are a data scientist clarifying the scope of a query.
Read the complete request, target and its exact question_focus. Propose the
schema-supported business entities, populations or attachment interpretations
that answer THIS scope decision. Use the request to constrain alternatives;
leave unspecified attributes, operations and values unspecified. A scope choice
does not choose returned fields. Do not invent identifiers or parameter values.
category_context, when present, constrains possible meanings through the supplied
relations. It selects no concrete field and does not resolve remaining choices.
For each meaning give the exact schema columns needed to support that meaning,
including necessary join keys. Dependencies are checked by code for access.
Return all relevant distinct scope choices, without imposing a menu-size cap;
the runner groups large domains. Return [] when no supported choice exists.
Describe query content in business language, with no external tasks or benefits.
Return only JSON: {"options":[{"text":"...","required_columns":["table.column"]}]}.
'''

STRICT_CHOICE_GROUPS = '''You are organizing business choices into narrower menus.
Derive categories from the supplied meanings, business descriptions, current
request and question focus; use the same language as the request. Summarize what
the choices in each group have in common with a short, distinguishable label.
Choose a useful business distinction for this level, rather than a fixed taxonomy.
Partition exactly the supplied choice_ids into 2..max_options distinct business
categories. Each category must be a proper, nonempty subset of the input. Use
every choice exactly once, without additions, omissions or duplicates. Retain
distinctions in the supplied meanings and the target's role in the whole request.
Labels name common business subjects; they do not promise that all descendants
will be returned. Do not invent query requirements, values or external tasks.
Prefer a few coherent groups that substantially narrow the next menu. When
semantically appropriate, distribute choices to avoid repeatedly peeling off one
choice from a large remainder. Preserve meaningful distinctions over equal sizes.
The runner will ask concrete choices inside every category the user selects.
Return only JSON: {"options":[{"label":"...","choice_ids":["c1","c2"]}]}.
'''

STRICT_FIELD_LABELS = '''You are presenting query fields in clear business language.
Render each supplied choice as a concise menu label answering question_focus for
the target fragment. Use the request's language and the supplied field meanings;
preserve distinctions such as entity, unit, time basis and aggregation when given.
Each label describes exactly its bound choice, without adding conditions, values,
external tasks or reasons to accept it. Keep unresolved details unresolved.
Return one distinct nonempty label per choice_id, with every supplied ID exactly
once. The runner owns the field bindings; return labels only, without regrouping.
Return only JSON: {"options":[{"choice_id":"c1","label":"..."}]}.
'''

CATEGORY_PROPAGATION = '''You are narrowing related query choices after menu navigation.
The user selected categories, not their concrete fields. Each source domain is
the union of still-possible choices across ALL selected branches. Use the whole
request, supplied linguistic relations and shared-decision evidence to retain
compatible candidates for each neighbor. Remove a candidate only when it is
incompatible with every remaining source interpretation through that relationship.
Different fields of one entity need not match or share a category. If the relation
does not constrain this choice, retain its whole domain. Do not transfer a source
field as a neighbor's chosen answer. An empty subset is allowed only when none fits.
Return each supplied neighbor exactly once, using only its existing column IDs.
No chosen fields, requirements, permissions or resolved decisions may be returned.
Return only JSON: {"items":[{"item_id":"o2","columns":["table.column"],
"reason":"brief relationship supporting the retained subset or unchanged domain"}]}.
'''
