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

