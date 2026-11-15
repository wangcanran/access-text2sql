DISCLOSURE_FRAME_PROMPT = '''You are a language analyst segmenting an original database query request
into units for controlled information hiding. Do not infer answers from SQL.

# Database
[[DB_name]]
# Schema and Foreign Keys
[[db_schema]]
# Column Meanings
[[data_description]]

Quote the request's independently specified return values in outputs, and its
conditions, grouping, ordering, limits and calculation scope in modifiers.
An output is what the answer must return (the SELECT expression), not every
attribute mentioned in the question. A modifier determines which records qualify,
how values are calculated, or how the result is organized. Determine this role
from the whole sentence before extracting its words. Attributes and parameters
used only to choose records belong to modifiers even when they are noun phrases.
Separate explicitly enumerated return values; do not wrap their entire enumeration
in a second output. Keep ranking and cardinality instructions separate from the
returned attribute whenever their words can be quoted separately. A returned
value is not the field used only to rank or restrict it. A count or average stays
with its operand as one calculation; a local restriction can be a separate span.
When one operator has scope over several enumerated operands, quote the operands
separately and leave the shared operator outside their spans; do not attach it
only to the first operand. Interpret each operand with that inherited operator.
An entity that only identifies the population is context, not an extra output.
A heading introducing an enumeration is not a second copy of the enumerated
requirements. Include an entity output only when returning that entity is asked
for independently of its named attributes. Use metadata only to understand words.

Each span is an exact contiguous quote. Keep a calculation and its operands
together, and a condition with its arguments. Separate independent specifications
when the words permit; keep compound logic intact when separation would lose scope.
Do not duplicate context or count a parent phrase together with its children.
No source character position may occur in two spans. Identical words occurring
at different positions are allowed. Preserve every explicit query specification;
commands, connective grammar and purely introductory headings need not be units.
Grammatical singular or plural is not an explicit result limit or tie policy.

Return only JSON: {"original_text":"exact complete request",
"outputs":[{"item_id":"o1","span":"exact quote"}],
"modifiers":[{"item_id":"m1","span":"exact quote"}]}.
Use o1,o2,... and m1,m2,... in textual order; empty arrays are allowed.
Do not rewrite, hide, map columns or detect ambiguity.

# Example
Request: For depots in the northern region, show depot codes and the number of
shipments heavier than 8 kg, ordered by that count descending.
Response:
{"original_text":"For depots in the northern region, show depot codes and the number of shipments heavier than 8 kg, ordered by that count descending.",
"outputs":[{"item_id":"o1","span":"depot codes"},
{"item_id":"o2","span":"the number of shipments"}],
"modifiers":[{"item_id":"m1","span":"For depots in the northern region"},
{"item_id":"m2","span":"heavier than 8 kg"},
{"item_id":"m3","span":"ordered by that count descending"}]}
The two return expressions are separate; the introductory population is not a
third return value. The weight restriction applies to shipments, not depots.
Only codes and a count are returned. Region and weight determine populations;
they do not become returned values because they name attributes.
Its local scope is read from the original sentence, not copied into every span.

# Original Request
[[user_request]]
'''

INVENTORY_PROMPT = '''You are a language analyst checking a source-fragment inventory.
Read original_text and all supplied fragments. Identify only fragments that add
no independent query specification: introductory headings whose content is fully
enumerated elsewhere, or population mentions already supplying context to named
return attributes rather than requesting an additional returned value.
Do not exclude an actual condition, calculation, attribute, limit, or independently
requested entity. Do not select hiding targets or rewrite the request.
Only an output introduction can be excluded, and its covering items must be
outputs. A returned attribute is never redundant merely because a condition,
grouping or ordering instruction also uses it. A request to list entities remains
an independent output even when those entities have filtering conditions.
For each redundant fragment, copy its exact supplied span into source_span and
cite the distinct supplied output IDs that carry its content
or the return attributes it contextualizes. All uncited fragments remain counted.
Return only JSON: {"context_items":[{"item_id":"o1",
"source_span":"exact span of o1","covered_by":["o2","o3"],"reason":"why this quoted fragment adds no independent specification"}]}.
Use {"context_items":[]} if no redundancy is established. Use supplied IDs only;
each context item appears once and covered_by cannot reference another context item.
'''

ROLE_PROMPT = '''You are a language analyst checking the roles of already quoted
fragments in a database query request. Use the complete original sentence.
Mark a fragment output only if the final answer explicitly returns that value,
entity, or calculation. Mark it modifier when it selects records or returned
entities, defines calculation scope, groups, orders, or limits results. A field
used to choose which entity to return is not itself a returned value unless the
request separately asks for that field's value. Do not add, remove, split, or
rewrite fragments. Do not infer roles from item IDs or existing buckets.
Determine the answer's grammatical object first, then ask whether each fragment
names a returned value or supplies a predicate on that object. In a question of
the form "which entity has the highest measure", the entity is the answer;
the measure and extremum select it unless the question also asks for the measure.

Return only JSON: {"roles":[{"item_id":"o1","role":"output"}]}.
Include every supplied ID exactly once. role is output or modifier.
'''

OBLIGATION_PROMPT = '''You are a language analyst reading an original database query request.
For every supplied fragment, state the query requirement it contributes
in the original sentence. Resolve its referent using the whole sentence, but do
not assign to this fragment a condition or comparison scope contributed by a
different supplied fragment. Those requirements belong to their own items and
may be hidden independently. Include an operation inherited from words outside
all supplied fragments: in an enumeration, a shared operator applies to each
member unless the wording says otherwise. Do not infer from SQL, add a
requirement, resolve an existing uncertainty, or rewrite the request.

Return only JSON:
{"obligations":[{"item_id":"o1","requirement":"complete requirement in the original request"}]}.
Include every supplied output and modifier ID exactly once, even when a modifier
is also mentioned as context for an output. Each requirement
must be a nonempty string. These are source requirements, not user replies.
'''

DIRECTION_PROMPT = '''You are a data scientist planning controlled information hiding.
Use the original request and supplied fragments only. The code has already chosen
selected_ids; plan one change for each. No SQL, database answers, or user replies
are involved. Source obligations help resolve context; source wording is authoritative.

For each selected item:
1. Identify what the source actually specifies: a named category, attribute,
   operation, predicate, value, scope, or quantity. Identify that specification.
   If it names only a category, that category is the available detail to hide;
   unspecified attributes or granularity cannot be removed again.
2. Replace that specification with a broader category or an open parameter.
   The original must be one possible completion among meaningfully different
   alternatives. Explain the dimension left open in direction, not replacement
   wording. A synonym does not open a choice.
3. In retain, preserve the surrounding population, the item's query role, and
   requirements not selected for hiding. A selected category may broaden to its
   parent category; it must not switch to a sibling category. A hidden aggregate
   can become unspecified statistics, not a request for individual records.
   A ranked subset with an open size remains a ranked subset.
4. Check the whole request. Protected spans retain their own specifications,
   referents, logical connections and inherited operations. Do not assign a
   selected detail to a protected item merely because its contextual description
   repeats that detail. Keep shared wording needed by protected requirements.
5. Use context_only only if no independent source specification can be hidden
   without changing protected requirements, or the supposedly hidden detail
   remains determined elsewhere. Do not invent specificity to avoid this outcome.
   Grammatical singular/plural alone is not an explicit limit.

Return only JSON:
{"directions":[{"item_id":"o1","action":"hide",
"direction":"the explicit specification to leave open and its broader domain",
"retain":"the remaining requirements"}]}
Include each selected ID exactly once and no others. action is hide or context_only.
For context_only, direction gives the reason and retain describes what stays
unchanged. All strings are nonempty. Code binds the source specification from
the item ID; do not copy it into an extra field. Do not rewrite yet.
'''

DIRECTION_REPAIR_PROMPT = '''You are reviewing a failed information-hiding plan
for an original database query request. No user has answered any question.
Read the original frame, each source obligation, the previous directions, and
the failed rewrite with its item-level review. Return corrected directions for
exactly the same selected IDs.

For each failed hide, first decide whether its own source fragment contributes
a query specification that can become unspecified while all protected and
context_only requirements remain intact. Grammatical number in an interrogative
is not an explicit result limit. A phrase that only repeats the answer type or
context fixed elsewhere may have nothing independent to hide. In that case use
context_only, with an explanation in direction.
If the detail is independently hideable, retain hide and correct its direction;
do not merely suggest a different wording for the same impossible change.
Describe the missing specification, not a proposed replacement phrase. In
particular, switching from a count to a list chooses a new return operation.
In local replacement mode, only the selected source spans can change. Check
whether those substitutions can still form a grammatical request for data. If
the fixed surrounding words make that impossible, retain the valid semantic
direction for a possible whole-sentence rewrite rather than approving a different
query operation. Use context_only only if there is no independent detail to hide.
Keep directions for successful items unless they conflict with the correction.
Do not add requirements or assume a database answer.

Return only JSON: {"directions":[{"item_id":"o1","action":"hide",
"direction":"missing query detail",
"retain":"meaning to preserve"}]}.
Every string is nonempty. Code binds source specifications from item IDs.
'''

REPLACEMENT_PROMPT = '''You are a language editor creating controlled ambiguity in a database request.
Read the complete original request, its fragments, source obligations and the
supplied hiding directions.
For each action=hide ID, write a replacement for that exact source span.
replacement_targets lists exactly the allowed IDs, source spans and surrounding
text. Return those IDs only, even if other phrases could be improved.
The runtime
will insert all replacements simultaneously at their original positions; every
other character remains unchanged. Read the left and right context before writing.

Make the planned specification unspecified while retaining the supplied retain
meaning. Preserve the business entity, grammatical role, referents and logical
scope. Selection conditions stay conditions; returned values stay outputs.
Check the assembled sentence's predicate arguments: a condition on source
records must still apply to those records, not to the new result description.
If hiding a return operation, use wording that leaves the operation open. A
request for a list of entities is a different operation from a count of them.
For an aggregate result, request relevant statistics about the same population;
this leaves the exact statistic open while still asking the database for data.
Entity details or a list are not broader versions of an aggregate result.
Do not ask what information the user wants or what should be requested; that
asks about the request itself rather than requesting a result.
When the original wh-phrase and its predicate are both replacement targets,
coordinate both replacements to keep an operation-neutral data request grammatical.
Use a broader description of the same object or an unspecified criterion, not a
new entity, statistic, threshold, tie policy, or stronger selection rule.
Fit the existing grammar, including inherited verbs and operations in enumerations.
If "number of" or another operation is outside the replacement target and
governs its enumeration, write an operand of that same operation. After "number
of", name countable business objects; a phrase such as "number of metrics"
would count the metrics themselves and change the query. Do not substitute a
measure name, descriptor, or summary for the objects being counted.
If the edited span supplies an operation shared by later enumerated items, retain
that operation for those items in the replacement. Do not turn protected counts
into bare entities by replacing their shared count expression with descriptions.
Match the source phrase's number, grammatical category and required preposition.
Outside verbs and articles cannot change: a plural replacement for a singular
subject, or a mass noun after a plural copula, is invalid. Read the assembled
sentence with all replacements before returning them.
Do not repeat adjacent words or absorb another fragment. If adjacent selected
spans interact, make their replacements grammatically compatible with each other.
No additional edits, full-sentence rewrite, placeholders or explanations.

Return only JSON:
{"replacements":[{"item_id":"o1","replacement":"replacement words"}]}.
Include each hide ID exactly once, no context_only or unselected IDs.
All values are nonempty strings. The complete original request must remain a
possible completion when the removed specifications are restored.
'''

REWRITE_PROMPT = '''You are a writer rewriting a database query request with controlled ambiguity.
Read the original request, fragments, and the approved hiding directions.
Produce one fluent, complete request that makes only the planned details
unspecified. Preserve all other requested values, calculations, conditions,
scope, and entity identities. Do not answer the query or invent a replacement fact.
When changing the output phrase, keep selection predicates attached to the
original records or entities, never to the result or statistic being requested.
The supplied protected_fragments are mandatory unchanged requirements, interpreted
in the original sentence. Copy each protected span verbatim, with the same referent
and attachment. Keep the source order and sentence construction wherever possible;
change selected phrases and only the grammar necessary to fit those changes.
Keep each protected specification explicitly recoverable in the rewrite,
including inherited operators in enumerations, its referent and local scope.
A broader category that could include a protected requirement does not preserve
that requirement. Hiding one enumerated item does not generalize its siblings.
Where a direction opens a choice, do not silently fill it with a different count,
list, comparison, ranking rule or output operation. Preserve specifications not
opened by that direction, including those inside a selected fragment.
A record-selection condition must remain a condition, not become information
to return about that condition. Keep pronouns and omitted arguments unresolved
when the source does not establish their referent; hiding is not an opportunity
to choose an interpretation of an already uncertain original sentence.
Express missing details naturally as general business descriptions. Do not include
placeholders, item IDs, explanations, or the hidden source specifications.
The rewritten sentence must directly request database results. For a hidden
aggregate operation, request relevant statistics for the same population, with
the particular statistic open. "Provide entity details" implies attributes,
not an aggregate; "what information should be requested" is a meta-question.
Apply directions together once. context_only
entries do not authorize extra semantic changes. Preserve wording outside the
selected phrases; adjust connecting grammar only when necessary for fluency.
This is an underspecified QUERY, not an instruction to redact its eventual answer.
Do not add instructions such as "without revealing values" or "omit the breakdown".
The original request must remain a possible completion of the rewritten request:
restoring the hidden specifications should recover it without removing any new
constraint. Avoid stronger or different selection rules when generalizing.
Return only JSON: {"rewritten_request":"the complete rewritten request"}.
'''

REVIEW_PROMPT = '''You are a reviewer of controlled ambiguity generation.
Compare original_text and every source fragment against rewritten_request,
using the supplied source obligations, not SQL or assumed database results.
You are not given the writer's intended hiding directions. Judge the actual
complete requests independently. For each hide ID, verify that some requirement
contributed by that item became unspecified in the rewrite while the original
request remains a possible completion. For every protected ID, verify its full
source requirement, logical connections and scope survive.
Source obligations are contextual descriptions, not independent additional
constraints. If an obligation repeats a detail explicitly supplied by another
fragment selected for hiding, do not require that detail to remain merely because
it appears in the protected obligation's wording. Judge ownership from the source
spans and interpret preserved references in the complete rewritten sentence.
When a return operation is hidden, check whether the rewrite actually leaves it
open. A question asking which entities to return specifies a list, not an
unspecified version of a request for their count; mark that substitution fail.
If the original returns an aggregate, a rewrite requesting entity details also
changes the answer type; a broader statistical result must admit the original
aggregate as a concrete completion.
Judge complete obligations in sentence context, not just whether their source
spans or nouns are present. If a shared operator was removed, check every member
that inherited it. A selected obligation still entailed by protected wording is
not hidden, even when the selected span was rephrased.
For each protected output, compare its requested operation as well as its noun.
If the source requires a count, sum, average, or other calculation, a rewrite
asking only for related information does not preserve that requirement.
Do not infer a quantity or tie policy from grammatical singular/plural alone.
Use two different tests:
- Protected: the rewrite must REQUIRE the same information or
  restriction. Mere compatibility is insufficient. Quote the rewritten wording
  that establishes it in evidence. A broad category containing the original
  attribute does not require returning that attribute; mark this fail.
- Hidden: an original specification of this item must be undecidable from the complete rewrite,
  while the source remains a possible completion. A new interpretation excluding
  the source is fail, even when the original detail no longer appears.
Read each source fragment in its original context, including inherited operations,
referents and scope. Do not assume omitted requirements from original_text apply
to rewritten_request. Check each enumerated sibling separately.
Judge whether a query specification is missing, not whether a database answer is
given: neither the original nor the rewrite normally contains execution results.
Equivalent paraphrases do not hide information. A synonym for an extremum still
specifies that extremum; a broader phrase hides it only if the criterion is open.
Also check the whole sentence for added requirements, entity substitutions,
changed AND/OR or negation, omitted outputs, altered calculation populations,
and contradictions. Fluent wording alone does not establish preservation.
Identify the subject of every selection predicate in both sentences. If a
predicate originally filters records but now describes the returned result or
statistic, mark that item fail even when the same condition words remain.
Check what each shared operator actually applies to after replacement: counting
business objects is different from counting metric labels or descriptions.
The rewrite must still be a grammatical request for data. A declarative sentence
with a question mark is not a valid underspecified version of a wh-question.
Check grammatical agreement, reference and natural phrasing in the complete
assembled request. A replacement that does not fit its unchanged surrounding
grammar is fail; name the mismatch in issues. Also reject added determinism or
other unstated result requirements, even if they sound like sensible defaults.
Completion test: could details from the original request fill the missing choices and
recover the original query without deleting an added constraint? If the rewrite
excludes records or readings the original permits, it fails even if it sounds
plausible. Check selection criteria separately from returned values and wording.
Do not treat a genuinely missing query detail itself as an error or resolve it.
Return only JSON:
{"checks":[{"item_id":"o1","result":"hidden","evidence":"brief comparison of the source and rewrite"}],"issues":[]}.
Use the literal key item_id in EVERY check. Include every ID from both frame
arrays exactly once, including context headings. result is hidden for successfully hidden
items, preserved for unchanged/context_only items, fail for a demonstrated error,
or uncertain if you cannot decide. issues contains specific sentence-level defects;
issues is an array of strings, NEVER objects; use [] when none are found.
Choose hidden/preserved from hide_ids and protected_ids. A hide ID that did not
become underspecified must be fail, even if its wording changed.
Do not provide a corrected rewrite.
'''
