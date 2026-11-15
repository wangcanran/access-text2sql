"""One complete decomposition contract; existing answer contracts are retained."""
from clarification_contract_v5.prompts import DECOMPOSE_BINDINGS


# Replacements below translate the inherited graph vocabulary. A missing
# source phrase must fail closed instead of leaving incompatible instructions.
_BINDING_REWRITE_SOURCES = (
    'Each plan node additionally returns binding_claims as supplied for the current\n'
    'parents. Each claim supplies meaning (business contribution text) and columns\n'
    '(real schema columns). Code compiles use and state from the node role/kind and\n'
    'column presence; use is returned|operand|condition|subject and state is\n'
    'bound|open in the canonical graph. Do not invent labels such as "subject binding".',
    'Connect each affected output to that leaf with [output,subject_leaf,"requires"].',
    'Optional filters use applies_to and cannot cancel outputs.',
    'Use applies_to only for a truly\n'
    'optional qualification whose removal leaves the requested output\'s identity\n'
    'unchanged.',
    'Do not attach the outpatient leaf with applies_to: losing it changes the',
    'If a NONE event is supplied, split its exact asked purpose from all other pending\n'
    'decisions BEFORE deletion. The seed leaf may own only the asked IDs. Other\n'
    'decisions belong to separate leaves; explicit requires can propagate necessary\n'
    'operand/subject loss.',
)
for _source in _BINDING_REWRITE_SOURCES:
    if DECOMPOSE_BINDINGS.count(_source) != 1:
        raise ValueError('Inherited decomposition bindings changed; review v10 prompt contract')

PLAN = '''Decompose the CURRENT request into independently meaningful business
contributions. Preserve every active parent's requested results and conditions,
including denied contributions. Code blocks questions before publication and
commits permission deletions only after eligible clarification ends. Do not ask
permission to remove unavailable requirements. Do not propose alternatives.

Return JSON only, exactly:
{"nodes":[{"id":"p1","parent":"f1","text":"Return employee phone numbers.",
"semantic_class":"output","kind":"attribute","columns":["employee.phone"],
"questions":{},"candidates":{},"decision_ids":[],
"binding_claims":[{"meaning":"Employee phone numbers","columns":["employee.phone"]}],
"affects":[]}],"retired_dependencies":{}}

Every node has exactly the fields shown. Do NOT return role, edges, edge kinds,
stable IDs, lifecycle flags or deletion decisions. Code derives these.
id is a unique temporary alias; parent is an existing active parent ID. Cover
EVERY parent, including all independent parts hidden in compound parents.
semantic_class is EXACTLY output, scope, requires, or annotation:
- output: one independently requested returned business result.
- scope: a necessary subject, population, filter, time range or locator.
- requires: necessary operand, formula, join, grouping or ordering contribution.
- annotation: display-only explanation whose absence cannot change population,
  filtering, subject, grouping, ordering or a calculation. A filtering predicate
  is NEVER an annotation. Losing a specified cohort must not return all records.
affects lists aliases of the contributions that consume this contribution.
Code compiles scope/requires providers to consumer -> provider requires edges;
annotation compiles to optional applies_to edges. Output affects is normally []
only when that output is independently meaningful. If an output says
"associated with each listed X", "for every returned X", "corresponding to
the selected X", or otherwise needs the result set of another output X, its
affects MUST list X's alias. This is a result dependency, not display context.
For example, if p1 returns the most common administrator names and p2 returns
the district associated with each of those names, p1 MUST declare
affects:["p2"]. If p1 is permission-blocked, p2 cannot survive independently.
Every modifier ultimately contributes to an output. An annotation affects only
returned outputs. Share necessary conditions through one provider, never copy
them into each result or register their question on each consumer.
Example: employee identifiers and phone numbers hired in a specified year become
two outputs and ONE scope node affecting both. Choosing the year is a question
owned only by that scope. Displaying identifiers does not make identifiers a
mandatory dependency of displaying phone numbers. Do not add entity-ID providers
merely to restate the subject when the request needs no ID-based locator.

kind is attribute/calculation/relation/instruction. text expresses the local
contribution; separately represented restrictions belong on scope providers,
not hidden inside generic output prose. Preserve full requested formulas.
questions maps scope/field/operation/value to nonempty business questions.
Use the existing pending decision IDs, routed within descendants of their own
parent and purpose. A shared missing fact has one owner; consumers do not copy
its question. Register genuinely new questions only during decomposition, not
as invented followups to already settled purposes. Unknown choices stay unknown;
decomposition does not answer questions, select all fields or invent thresholds.
candidates maps exact schema table.column to business labels, only when field
is pending. columns contains confirmed mandatory dependencies, NOT all options.
This is a hard distinction: when a claim has state="open" and use="returned"
or use="operand", its columns array MUST be empty. Put unresolved field choices
only in candidates and keep the corresponding question open. Never copy the
first, most likely, primary-key or joinable column into columns while the field
choice is open. A mandatory subject, join or filter dependency that is already
known must be represented by a separate bound subject/condition claim; it must
not be attached to an open returned claim. A claim with state="bound" may list
only the exact dependencies already established by the request or a prior
answer. The following is invalid: state="open", use="returned",
columns=["schools.CDSCode"]. The corrected form has columns=[] and puts
schools.CDSCode among candidates when it is merely one possible return field.
Keep previous required columns on appropriate descendants. If (and only if) a
previous physical dependency is proven to be an irrelevant speculative binding,
retire it in the exact map shape
{"table.column":"why this prior dependency is no longer needed"}.
The key is the physical column and the value is one nonempty reason string;
do not use parent IDs or arrays of columns.
Never retire necessary subject/condition dependencies to bypass permissions.
Use public request, schema and actual answers, never private intended answers.
'''

# Reuse binding/subject semantics without leaving a conflicting output format
# that would ask the model to generate edge direction or optional predicates.
BINDINGS = DECOMPOSE_BINDINGS.replace(
    'Each plan node additionally returns binding_claims as supplied for the current\n'
    'parents. Each claim supplies meaning (business contribution text) and columns\n'
    '(real schema columns). Code compiles use and state from the node role/kind and\n'
    'column presence; use is returned|operand|condition|subject and state is\n'
    'bound|open in the canonical graph. Do not invent labels such as "subject binding".',
    'Each plan node additionally returns binding_claims for its current parent.\n'
    'Every claim explicitly supplies meaning, real schema columns, use and state;\n'
    'the pool contract also requires obligation_ids. use is returned|operand|\n'
    'condition|subject and state is bound|open. Code validates and preserves\n'
    'these declarations; do not invent labels such as "subject binding".').replace(
    'Connect each affected output to that leaf with [output,subject_leaf,"requires"].',
    'List each affected output alias in that scope leaf\'s affects.').replace(
    'Optional filters use applies_to and cannot cancel outputs.',
    'Only display-only annotations are optional. Scope filters remove dependent outputs.').replace(
    'Use applies_to only for a truly\n'
    'optional qualification whose removal leaves the requested output\'s identity\n'
    'unchanged.',
    'Only display-only explanations are annotations. A business qualification\n'
    'that changes which records qualify is a required scope provider.').replace(
    'Do not attach the outpatient leaf with applies_to: losing it changes the',
    'Do not mark the outpatient leaf as annotation: losing it changes the').replace(
    _BINDING_REWRITE_SOURCES[-1],
    'For NONE, route the asked decision IDs to their contribution leaf. Other\n'
    'pending field/value/operation questions may stay on that leaf only when all\n'
    'its claims refine the same single inherited contribution ID. Split\n'
    'independent contributions before pruning; the code owns deletion closure.\n')

REVIEW = '''
This run uses USEFUL RESIDUAL semantics. All necessary population, subject,
filter, time, grouping, ordering and calculation restrictions are HARD. Only
display-only annotations may disappear without their output. A denied cohort
must not be broadened to all records. Judge deletion and candidate text under
this policy. Do not demand answers on correctly blocked leaves. Stable question
IDs and answer settlement remain governed by the existing light ledger.
permission_facts is computed by code using exact membership in allowed_columns.
denied_dependencies are inaccessible even if those columns exist in schema,
canonical_nodes, historical bindings or another table with a similar name.
Do not recompute authorization or override these facts. Review whether the
declared business dependencies and deletion coverage are semantically justified;
an inaccessible provider cannot by itself justify deleting unrelated outputs.
'''

BINDINGS += """
For each inherited subject_obligation, preserve its exact meaning string in
exactly one non-relation modifier binding_claim, with all its original columns.
Different business claims may legitimately share a column (e.g. returning a
date, filtering its range, and ordering by that date). Do not duplicate the same
subject obligation across providers. Each output must depend on EVERY inherited
subject provider; an edge to just one does not preserve the others.
"""
