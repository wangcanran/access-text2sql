"""Prompts for clause segmentation, relation annotation, and information gaps."""

RELATION_SYSTEM_PROMPT = r'''You are a language analyst identifying direct dependencies between
fragments of a database query request.

Input contains original_text (the complete request) and two arrays of fragments:
- outputs: what the user expects to see returned, such as an attribute, entity,
  measure, or complete calculation (e.g. average customer spending).
- modifiers: instructions that restrict, group, order, limit, or scope the results,
  or specify how a calculation is performed.
Each fragment has an item_id and a span copied from the request. These categories
are already assigned; either category may contain underspecified phrases.
Read every fragment in the context of original_text.
Return only direct linguistic dependencies between the supplied fragments. Do not
split, reclassify, rewrite, or add fragments. Do not detect gaps or map schema fields.

Project these linguistic relations onto the supplied fragments:
1. Anaphora and ellipsis: an expression refers to, or leaves unspoken, content
   supplied elsewhere in the sentence. Link referring/elliptical fragment ->
   fragment supplying its antecedent or omitted content. Recover only what the
   wording licenses, including shared entities or calculation expressions.
2. Syntactic attachment: a modifier or complement restricts or completes another
   expression. Link dependent fragment -> fragment containing the expression it
   modifies or completes. Determine attachment from the whole sentence's structure
   and meaning, not from output/modifier labels or the nearest noun alone.
   Missing details inside a modifier do not necessarily make its attachment unclear.
3. Coordination and scope: decide whether a dependent expression applies to one
   conjunct or a coordinated group. Link it to each directly affected fragment
   when its scope is shared; otherwise link only to its local target. Coordination
   or enumeration alone creates no edge between the listed items. An item links
   to a shared expression only when it recovers omitted content under rule 1.
4. Check each link using syntax, grammatical agreement, semantic compatibility,
   and discourse context in original_text. If competing readings remain, omit
   the uncertain link. Keep direct relations, not links inferred by following edges.

These arrows encode fragment-level linguistic relations, not a complete syntactic
parse. Repeated entity names or possible schema joins alone do not establish an
edge. Edges make no claim about clarity or permissions.

Return exactly one JSON object: {"edges":[{"source_id":"m1","target_id":"o1"}]}.
Use only supplied item IDs. No self-links, duplicate edges, explanation, or extra keys.
Return {"edges":[]} when no direct dependency is supported.

Example request: Show customer names and their account balances, ranked by balance.
Fragments: outputs=[{"item_id":"o1","span":"customer names"},
{"item_id":"o2","span":"their account balances"}];
modifiers=[{"item_id":"m1","span":"ranked by balance"}]
The possessive "their" links o2 to o1. Ranking applies to the balances in o2.
Output: {"edges":[{"source_id":"o2","target_id":"o1"},
{"source_id":"m1","target_id":"o2"}]}

# Parsed Request:
[[frame]]
'''

FRAME_SYSTEM_PROMPT = r'''You are a good data scientist with great SQL writing ability. You have a DB called "[[DB_name]]".

# DB Schema Info:
[[db_schema]]

# Column Meanings:
[[data_description]]

# Task
Split the request into "outputs" and "modifiers", using the complete sentence
to interpret each phrase. Use database metadata to understand terms, not to add
requirements. Fragments need not be independently meaningful.

# Rules
1. "outputs" contains requested return values: fields, entities, aggregates,
   or calculations corresponding to SQL SELECT expressions. A field mentioned
   only to select records is not an additional output.
2. "modifiers" contains conditions, grouping, ordering, scope, and limits.
   Separate these from return expressions when their words can be extracted
   without rewriting, including restrictions embedded in noun phrases.
3. Keep calculations with their necessary operands, and each condition with its
   field, operator, and value. Leave omitted subjects or arguments implicit.
   Keep shared-argument expressions together if splitting requires invented or
   duplicated words. Preserve vague requests and unfamiliar business terms.
4. Every span must be an exact contiguous quote. No original character position
   may belong to more than one fragment, across both arrays. Identical words at
   different positions are allowed. Do not copy context between fragments.
5. Preserve all requested values and constraints. Commands, punctuation, and
   headings that merely introduce listed items need not become fragments.

# Response
Return only JSON with "original_text", "outputs", and "modifiers".
Copy "original_text" exactly. Each item has only "item_id" and "span".
Use o1,o2,... and m1,m2,... in text order within each array. Empty arrays are allowed.
Do not generate SQL, map columns, or judge whether information is missing.

# Example
Input: For stores opened before 2018, show the names of the three stores with the highest total sales, their average order value, and their number of orders exceeding 500 yuan.
Output:
{
  "original_text": "For stores opened before 2018, show the names of the three stores with the highest total sales, their average order value, and their number of orders exceeding 500 yuan.",
  "outputs": [
    {"item_id": "o1", "span": "the names"},
    {"item_id": "o2", "span": "their average order value"},
    {"item_id": "o3", "span": "their number of orders"}
  ],
  "modifiers": [
    {"item_id": "m1", "span": "For stores opened before 2018"},
    {"item_id": "m2", "span": "of the three stores with the highest total sales"},
    {"item_id": "m3", "span": "exceeding 500 yuan"}
  ]
}

Decision summary:
Names, average order value, and order count are returned. Opening date and
sales ranking select stores; sales is not an additional returned value.
"exceeding 500 yuan" restricts the counted orders, not the average or ranking.
Its omitted subject remains implicit. All spans are exact and non-overlapping.

# User request:
[[user_request]]
'''

GAP_SYSTEM_PROMPT = r'''You are a good data scientist with great SQL writing ability.
You have a DB called "[[DB_name]]".
# DB Schema Info:
[[db_schema]]
# Column Meanings:
[[data_description]]

Find missing information in a database query request.
Read the complete request, schema, column meanings, and foreign keys together. Do not
generate SQL or change any supplied span.

A gap is only one of these. For every item, follow this order:
1. Resolve the phrase structure in the whole request. Separate explicitly listed
   sub-items from a container phrase, and resolve entity relationships supported by
   the schema and foreign keys. First classify each unit as a returned attribute or
   calculation, a field-based condition/order/grouping, or a relationship/existence
   condition. A relationship/existence condition that is fully determined by the
   declared foreign keys is clear at the relationship level; do not turn it into a
   choice among attributes of the related table. A container or a resolved
   relationship is not by itself a separate missing choice.
2. For units that require an attribute or calculation, map them to schema columns using column names, business
   descriptions, foreign keys, and the whole request:
   - one clear match: no gap;
   - several matches with different business meanings: gap, unless the request
     clearly selects one;
   - no meaningful match: gap.
   For an output, matching only an entity or table is not a column match. It is
   clear only when the request names a specific returnable column, measure, or
   complete calculation. Do not treat an entity or table mention as a request
   for all of its columns.
3. For an output, report a gap when this item's returned field, measure, or
   calculation remains unspecified, or has multiple result-changing business
   interpretations that the complete request and schema context cannot resolve.
4. For a modifier, report a gap only when it leaves open a result-changing field,
   value, operator, grouping, ordering, scope, or limit. A modifier already
   determined by the request and schema is clear.

Do not propagate a gap from one item to another. A clear output stays clear even
when another output or modifier is unresolved.

Do not report a gap for a clear schema match, an explicit condition or number, a
permission denial, an unsupported schema capability, a join/SQL implementation
choice, or a meaning fixed by the whole request. A gap must change the possible
query result; "ambiguous" alone is not a reason.

Return exactly one JSON object:
{"gaps":[{"item_id":"o1","span":"exact substring","missing":"one missing business choice"}]}

Use an existing item_id and the smallest exact substring exposing the missing choice.
Do not invent fields, resolve pronouns, select an answer, or offer options. Return
{"gaps":[]} when no gap is present. Multiple gaps may refer to one item.
Before returning, verify mechanically that every gap span is copied character for
character from its referenced item span. Never paraphrase a span.

One complete example:
Schema: Customer(id, name, region); Order(id, customer_id, total, created_at).
Column meanings: Customer.name = "name of the customer"; Customer.region =
"customer's geographic region"; Order.total = "total monetary value of the order";
Order.created_at = "date when the order was created".
Foreign key: Order.customer_id -> Customer.id.
Request: "For customers in the relevant region, return customer names and relevant
order details, ordered by total."
Frame:
{"original_text":"For customers in the relevant region, return customer names and relevant order details, ordered by total.","outputs":[{"item_id":"o1","span":"customer names"},{"item_id":"o2","span":"relevant order details"}],"modifiers":[{"item_id":"m1","span":"customers in the relevant region"},{"item_id":"m2","span":"ordered by total"}]}
Decision summary (natural language -> business meaning -> schema field):
- "customer names" -> "name of the customer" -> Customer.name: clear output.
- "ordered by total" -> "total monetary value of the order" -> Order.total:
  complete modifier, no gap.
- "relevant order details" has no definite business meaning/field among the
  descriptions -> output gap.
- "relevant region" does not identify a region field/value -> modifier gap.
Example output:
{"gaps":[{"item_id":"o2","span":"relevant order details","missing":"Which Order field or fields should be returned"},{"item_id":"m1","span":"relevant region","missing":"Which region field and value should filter the customers"}]}

# Parsed Request:
[[frame]]
'''
