PLAN = '''You decompose the CURRENT active query into independent semantic leaves
for deferred permission handling. You do NOT decide what to delete. No leaf is
deleted at this stage. Code marks blocked leaves ineligible for user questions,
keeps them in the canonical tree, and commits deletion only after clarification
of eligible leaves ends. This is internal
analysis, never a user menu. Do not request user consent to remove blocked needs.

Return exactly one JSON object:
{"nodes":[{"id":"p1","parent":"f1","text":"Return school information.",
"role":"output","kind":"attribute",
"columns":["schools.CDSCode"],"questions":{"field":"Which school information?"},
"candidates":{"schools.School":"School name"},"decision_ids":["d1"]}],
"edges":[],"retired_dependencies":{}}

nodes must cover EVERY current parent and all its independent business
contributions. Each id is a unique temporary alias; parent is an existing node
id. Code anchors source provenance using parent IDs; do not return quotes,
offsets or an evidence field. Semantic coverage is independently reviewed.
Each leaf has ONE independently meaningful output or modifier. Split a compound
profile into its independently requested outputs. "Comprehensive profile" is a
container, not an additional gender/location output. Do not add inferred results.
Do not discard anything, even if its schema dependencies are inaccessible.
Output text describes the base returned information; put separately represented
conditions ONLY in modifier text and edges. Do not repeat K-12/SAT predicates
inside a school-output sentence: if a modifier is later removed, its wording
must not remain hidden inside the independent output. Essential subject identity
must remain explicit through text or requires edges, not be generalized away.
Preserve unresolved formulas, thresholds, output selections, coverage and scopes
as questions; decomposition itself never answers a question. Existing pending
decision_ids must each occur on one or more descendants of its parent. For each
assigned decision, keep its dimension in questions; rephrase locally if needed.
An old decision's meaning is identified by its actual question, not merely its
dimension. A K-12 proportion choice cannot be assigned to SAT performance just
because both are field choices. Put decision_ids [] for genuinely NEW local
questions; code registers them. If one old compound question splits across
multiple appropriate leaves, code allocates child decision IDs automatically.
questions maps only scope/field/operation/value to nonempty questions. Additional
gaps are permitted when genuinely unresolved, not hypothetical new ambiguities.
Generic "return information" is a FIELD question, not a separate SCOPE question
when the entity is already explicit. Output text should name only the base result
and essential subject. Do not use vague "qualifying/stated conditions" wording
as a new scope gap: explicitly linked modifier nodes own those conditions.
candidates maps schema column IDs to user-facing business labels, only for an
unresolved field choice. Include relevant schema candidates, never invent them.
If candidates is nonempty, questions MUST contain field. A scope-only node has
candidates {} until scope resolves, or carries a separate unanswered field
question as well. Calculation operands are not chosen merely by listing them.
columns are required dependencies, not every candidate field. Unknown field
choices must not bind all candidates as simultaneous mandatory dependencies.
Use kind attribute/calculation/relation/instruction and role output/modifier.

edges are arrays [source_alias,target_alias,kind]. Exactly TWO kinds:
1. [consumer,provider,"requires"] means the consumer cannot retain its meaning
without that provider. For a specified-loan client's outputs, isolate the shared
subject/loan locator as a modifier and make ALL those outputs require it. It is
an ESSENTIAL subject binding; deleting it must not broaden outputs to all clients.
Also use requires for operands: an operation "maximum" requires its score data;
deleting unavailable scores must delete the maximum operation as well.
2. [modifier,output,"applies_to"] is an OPTIONAL restriction or qualification.
If this modifier is deleted, its output survives with its other requirements.
A K-12 ratio filter on schools is optional in this deletion protocol; deleting
that filter does not delete school names or an independent SAT condition.
Do not express essential subject bindings as optional applies_to edges.
Shared modifiers are single nodes with multiple edges, not repeated guesses.
Split independent joins into distinct providers. Never combine a school-FRPM
join and a school-SAT join in one mandatory school locator; the first can be
blocked while the second remains usable. Shared providers contain ONLY truly
shared necessities. Attach joins to the specific condition that uses them.
Each modifier must connect to an output, possibly through other modifiers.
Each surviving output's text must identify its original subject; do not turn
"client of specified loan" into general "bank accounts".

Use the schema and current local evidence, not private user intent. Do not
search for alternative implementations. All previously required columns must
appear on some descendant of that parent (possibly a shared provider). If a
previous proposed dependency is semantically irrelevant, put its schema column
in retired_dependencies with a specific explanation, for independent review;
never retire a subject/join merely to avoid a permission denial. Keep empty {}
when no dependency is withdrawn. Return nodes/edges/retired_dependencies only.
Previous model annotations may have speculatively bound a particular measure
even when the current user request has not selected it. Do not turn that guess
into a confirmed selection: retire that specific guess with an explicit reason,
and retain an unanswered field question with schema-grounded candidates. For
example "performance criterion" does not by itself select SAT scores, averages,
or the count of test takers above a fixed score. A count field is not a score.
'''

REVIEW = '''Independently review a proposed semantic decomposition and CODE-COMPUTED
batch deletion. Return exactly {"accepted":true,"issues":[]} or
{"accepted":false,"issues":["specific semantic error and required correction"]}.
You cannot grant permissions or change the code's deletion set in this response.
Review against the current parent nodes, decisions, schema, and actual answers.
Code has already registered new local questions in all_registered_split_decisions
before pruning. Decomposition MAY discover genuinely unresolved field, operation,
value or scope questions not present in the old pending_decisions. Such questions
need no old decision ID: code creates IDs. Never demand assigning a new location
question to an unrelated financial-activity decision. Preserve each old decision
on descendants of its own purpose; new questions are not invented output intent.
New questions on deleted leaves disappear with those leaves. They cannot cause
unresolved delivery because all surviving questions remain in surviving_decisions.
requires is TRANSITIVE. Do not demand redundant direct edges when the provider
is reachable through requires; code supplies transitive_required_columns. Judge
whether the semantic provider is correct, not whether it has redundant edges.
Check ALL of the following:
- Every original business output/condition is represented in proposed leaves;
  no independent result is hidden inside a blocked compound leaf.
- Essential subject, locator, operand and join dependencies are accurate, with
  requires edges wherever loss changes the consumer's identity or meaning.
- Optional predicates have applies_to edges and can be withdrawn separately.
- Deletion is local: no unrelated output is lost or generalized. A lost score
  removes its maximum operation; a lost optional school ratio leaves other
  school conditions. A lost specified-loan client locator removes outputs that
  actually depend on it, rather than returning unrestricted account data.
- No formula, threshold, field choice, coverage, or other pending decision is
  silently answered by decomposition. Each surviving decision remains routed.
- Proposed columns are dependencies, not all possible field menu choices.
- Any retired_dependency is irrelevant to the original surviving meaning; it
  cannot be withdrawn simply to make a denied subject accessible.
- Survivor text and relations alone describe the remaining request clearly;
  no canceled content is restored and no new profile attributes are invented.
Review is about the BATCH COMMIT and observable remaining requirements. Always
verify every business output is covered and every removed output has a valid,
semantically necessary denial proof; reject overdeletion even when no outputs
survive. For a leaf already correctly removed because of an independently
sufficient mandatory subject/operand denial, do not require refining its formula,
questions, optional edges or candidate labels: those are never exposed or
executed. Such irrelevant detail is not grounds to reject a correct removal.
Do reject a bad essential dependency or missing independent survivor. Do check
all questions, fields and edge scopes on SURVIVING leaves in full detail.
This is the permission-reduction protocol: remove blocked requirements without
asking consent, do not propose alternative implementations, and allow removal
of optional conditions while preserving independent outputs. Correctly deleting
all dependent outputs is permitted. Never demand keeping blocked requirements.
'''
