"""Separate contracts for structure proposals, local binding and rendering."""

SPLIT = '''Propose BUSINESS boundaries for this compound requirement, without schema
mapping. Return JSON {"children":[{"text":"business meaning", "role":"output|modifier",
"compound":false, "quotes":["exact source substring"]}]}.
Cover every non-whitespace source character with quotes; punctuation/headings may
be included in a neighboring quote. Quotes may overlap for shared subjects. Do not
manufacture outputs from subjects, operands, filters or headings. Each child owns
one business contribution; shared conditions get ONE modifier, never duplicate
the same unresolved argument in multiple outputs. A child may remain compound=true
when further business decomposition is needed. Never return a single unchanged
compound child. Do not resolve ambiguous scope or infer attachment edges. The
proposal will be confirmed by the user, not accepted as semantic truth.
'''

LOCAL = '''Analyze ONLY the target business node using confirmed structural context.
Return JSON {"columns":["table.column"], "questions":[
{"dimension":"field|operation|value", "question":"business question",
 "options":[{"label":"business meaning", "columns":["table.column"]}]}]}.
columns are determined dependencies, including subject/join/time operands, NOT
hypothetical alternatives. Every field question has business-labeled options with
complete additional dependencies. Operation/value questions have options=[].
Keep independent unresolved formula, subset, coverage, time and parameter issues
as separate questions even when a field menu is offered. Do not invent unspecified
requirements or assume that selecting a field answers coverage/formula questions.
Do not change roles, node boundaries or relation scope. Shared modifier meaning
belongs to its owner; do not duplicate its questions here. A clear node may have
questions=[]. Use schema identifiers exactly. Never expose permission reasons or
physical schema identifiers in public questions/labels. No SQL.
'''

DETAIL = '''Interpret the real answer to this ONE local question. Return JSON
{"resolved":true, "answer":"normalized business detail", "columns":[],
 "remaining_question":""}. columns contains additional determined dependencies
introduced by this answer. Existing dependencies are retained. If only partially
answered set resolved=false and provide the remaining business question. Do not
resolve any other question, change node roles/relations, or invent an answer.
If the answer requires changing structural scope, keep resolved=false and explain
the remaining structural uncertainty in remaining_question. No SQL.
'''

RENDER = '''Render the confirmed active graph as one precise natural-language request
for Text2SQL. Return JSON {"text":"..."}. Read ONLY supplied active nodes, confirmed
edges and accepted local answers. Modifiers are not outputs. Preserve exact
attachment scope, shared subjects, formulas and values. Do not restore retired
requirements, turn operands into outputs, or guess missing original intent.
Do not output SQL, schema identifiers, permission explanations or commentary.
'''

SCOPE = '''Interpret the user's scope explanation using ONLY current candidate outputs.
Return JSON {"selected_outputs":["current node ID"], "resolved":true}.
Do not rewrite nodes or introduce dependencies. If the explanation does not fully
identify an applicable subset, or requires missing business outputs, return
resolved=false and selected_outputs=[]. This is a PROPOSAL: the user will confirm
the proposed subset before any edge is committed. Never treat NONE as deletion.
'''
