DECISION_CONTRACT = '''
ADDITIONAL REQUIRED OUTPUT: clarification={"dimension":"scope|field|operation|value",
"followups":{"value":"Which concrete label/value should this condition use?"},
"continue_decisions":["d2"]}.
Return this alongside the existing decision fields; no extra envelope.
clarification.dimension describes WHAT THE PROPOSED QUESTION ACTUALLY ANSWERS.
field chooses literal input/output attributes; scope chooses a business entity;
operation specifies a statistic, formula, ranking/comparison or grouping method;
value specifies a literal category label, threshold, date or parameter.
Correct an initially misclassified dimension rather than force it into a menu.
A request for an unspecified summary measure is an OPERATION question: ask what
statistic/formula to compute, not a field menu offering counts, heights, weights.
The number of entities is a calculation, not the meaning of an identifier column.
For operation/value set field_candidates=[]; ask an open business question.
The legacy prohibition on values/formulas in this call applies ONLY to menu
questions; operation/value classification explicitly replaces that prohibition.

followups declares unanswered decisions that MUST remain after this selection.
Input pending_decisions provides existing stable IDs. Reuse an existing question
through continue_decisions instead of copying/rephrasing it into followups.
Use followups ONLY for genuinely NEW uncertainty not covered by those IDs.
Never repeat this question itself as its own followup. A followup is conditional
future work; it must not duplicate another pending purpose in the same node.
It may be {} only when selecting the proposed option fully answers this purpose.
Choosing a CATEGORY FIELD does not supply its literal value: retain a value
followup unless a concrete value is already explicit in the current request.
Choosing an ENTITY does not choose its returned attributes: an output scope menu
must retain a field followup asking names, identifiers, or other desired details.
Choosing operands does not define their calculation: retain operation/value
followups when formula or parameters remain unspecified.
Never invent a value or assume all. Do not ask for already explicit facts.
Menu labels must describe literal business attributes, never silently replace
an entity choice with selection of its numeric primary key as returned output.
When the public request asks to name related entities or provide unspecified
business identifiers, include human-readable names as well as available numeric
IDs where relevant. The word identifier does not prove a numeric primary key was
selected. Consult the provided schema meanings rather than narrowing by that word.
All option support comes from the supplied local schema and business context.
'''

UPDATE_CONTRACT = '''
CLARIFICATION IDENTITY CONTRACT:
The input mandatory_followups are not optional model hints. After an actual
menu selection they MUST remain unanswered, even if you omit child questions.
For a split, return optional top-level followup_routes={"value":"p2"} mapping
EVERY mandatory continuation dimension to ONE child alias of the answered parent.
For a single child code assigns ownership; omit followup_routes.
Existing continued_decisions must keep their IDs; do not re-add them as child
questions. Child questions contain ONLY newly discovered uncertainty. To reword
or route old obligations use question_changes by ID, not a copied aggregate.
Never close value or operation from a field/entity option unless the CURRENT
answer literally provides that fact. A category field is not its category value.
Scope labels identify entities, not which attributes to return. Keep a field
question until those attributes are selected or explicitly supplied.
Represent unresolved meaning honestly in child text as well as questions.
A new question may have the same dimension as the answered question: for example,
choosing a ranking method does not supply a tie rule. Do not re-ask an already
answered fact. Preserve independent pending decisions by ID on descendants.
'''
