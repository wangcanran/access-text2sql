FUSED = '''You refine and analyze only the editable region of a database request.
The request has already been rebuilt from effective dialogue and protocol removals.
In ONE response, quote its surviving local pieces, map their business meanings to
schema, and account for each pending decision. Boundary nodes are read-only.

Return only JSON with exactly these keys:
{"pieces":[{"piece_id":"p1","parent_id":"old editable ID","span":"exact current quote",
"open_choices":[{"dimension":"scope|field|operation|value","span":"exact local quote",
"question":"unanswered business question"}]}],
"items":[{"item_id":"p1", ...analysis fields described below...}],
"edges":[{"source_id":"p1","target_id":"boundary ID or p2","kind":"allowed relation kind"}],
"decision_effects":[{"decision_id":"d1","action":"keep|answered|removed",
"piece_ids":["p1"],"witness":"","reason":"brief evidence"}]}.
Use unique p1,p2,... local IDs in piece order; do not invent stable fragment IDs.
Each surviving editable parent needs at least one piece; removed_ids have none.
Never merge different parents or return boundary pieces/rows. Pieces quote CURRENT
request exactly. Preserve compound logic and qualifications; split only as needed
to independently handle answered and still-unanswered contributions. Each piece
has one analysis row. Edges use local IDs or existing boundary IDs.

Account for EVERY pending decision owned by an editable parent exactly once.
keep: map it to one or more pieces of the SAME parent, empty witness. It stays
unanswered even if a likely schema match exists or the rebuilt wording omits it.
answered: empty piece_ids, witness copied exactly from current answer_witnesses;
only the actually asked dimension/slots of the answered parent qualify. An answer
to a field question cannot resolve quantity, calculation, or another output.
removed: empty piece_ids, witness copied exactly from removal_quote; only owners
explicitly dropped/revised by this removal qualify. For a revised compound parent,
remove only decisions belonging to the deleted contribution; preserve the rest.
A navigation category is not a field answer. Do not treat schema as user evidence.
Record NEW uncertainty in open_choices and missing; do not duplicate kept questions.
Use scope for entity/population, field for business attribute, operation for a
calculation/comparison, value for quantity/threshold/date/coverage parameters.
'''
