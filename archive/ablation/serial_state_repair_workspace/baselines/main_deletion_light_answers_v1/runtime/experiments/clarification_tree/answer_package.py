"""Compile answer-owned refinement on private state; publish only the final tree."""
from copy import deepcopy
from .contribution import apply
from .transaction import commit
from .state import require, validate
from .focused_runner import INTERPRET

PROMPT = INTERPRET + '''
EXPERIMENT RESPONSE CONTRACT overrides the top-level response shape above:
return {"update":{the answer interpretation specified above},"refinement":null}.
In this experiment you may also split the UPDATED focus immediately, in the SAME
response, when the answer makes independent contributions clear. Then refinement is:
{"children":[{"meaning":"complete local contribution","granularity":"atomic",
"role":"output","columns":[],"scope_columns":[],"computation":{},"output_exclusions":[]}],
"issue_owners":{"remaining focus pending ID":0},
"new_issues":[{"owner":0,"dimension":"value","question":"remaining uncertainty"}],
"relations":[{"source":0,"target":1,"kind":"applies_to"}],
"coverage":"where the updated focus survives"}.
All five refinement keys are REQUIRED, even when empty: children, issue_owners,
new_issues, relations, coverage. Use {}, [], [] for the three empty collections.
relations.kind is exactly "applies_to" or "depends_on". Do not invent relation
names. Shared client, loan, table or join keys do not create dependence between
independent outputs: preserve each result's subject wording and use relations=[].
Children are a nonempty array, preferably atomic when semantic boundaries are clear;
keep names and corresponding values together. Do not create independent results
for operands of one calculation. Code assigns aliases and node IDs; owner/source/
target here are zero-based child indices, never IDs or strings. Relations are
UNCONFIRMED proposals and never resolve scope by themselves. No outside node edits.
Use refinement only when update.resolved=true. Put additional pending questions in
refinement.new_issues, leaving update.issues empty. issue_owners covers every OLD
focus pending issue still pending after update (exclude the current asked ID and
issues closed by update.issue_resolutions). Map each to exactly one child.
All updated focus meanings, inherited/selected fields, exclusions, subject/time
constraints and computations must survive. Remainders from the approved plan are
read-only and excluded from this refinement. Compound children may only retain
established fields; do not enumerate guessed candidates. Atomic children may bind
definite schema-supported fields without another question. Preserve all required
join/subject dependencies. Unknown fields/formulas/values remain explicit questions.
Do not invent additional questions merely to fill the structure. If the updated
focus is already one clear atomic result, use refinement=null: update itself can
include its bindings, formula and all supported local issue resolutions.
'''


def apply_package(state, plan, event, context, raw):
    require(isinstance(raw, dict) and set(raw) == {'update', 'refinement'}, 'Invalid answer package')
    # Existing interpreter validation (including exact evidence and no sibling
    # edits) stays authoritative. No intermediate state escapes this function.
    interim = apply(state, plan, event, context, raw['update'])
    refinement = raw['refinement']
    if refinement is None:
        return interim
    require(raw['update']['resolved'] is True and not raw['update'].get('issues'),
            'Refinement requires answered current question; new issues belong to children')
    require(isinstance(refinement, dict) and set(refinement) ==
            {'children', 'issue_owners', 'new_issues', 'relations', 'coverage'}, 'Invalid refinement package')
    children = deepcopy(refinement['children'])
    require(isinstance(children, list) and children, 'Refinement needs children')
    aliases = ['part' + str(i) for i in range(len(children))]

    def alias(index):
        require(type(index) is int and 0 <= index < len(aliases), 'Invalid refinement child index')
        return aliases[index]

    target = interim['audit'][-1]['focus_id']
    parent = interim['nodes'][target]
    for i, child in enumerate(children):
        require(isinstance(child, dict) and 'alias' not in child, 'Child aliases are code-owned')
        child['alias'] = aliases[i]
        child.setdefault('columns', [])
        child['output_exclusions'] = list(dict.fromkeys(parent['output_exclusions'] + child.get('output_exclusions', [])))
    require(set(parent.get('scope_columns', [])) <=
            {c for child in children for c in child.get('scope_columns', [])},
            'Refinement lost established subject/scope dependencies')
    pending = {i:q for i,q in interim['issues'].items() if q['owner'] == target and q['status'] == 'pending'}
    owners = refinement['issue_owners']
    require(isinstance(owners, dict) and set(owners) == set(pending), 'Every remaining focus issue needs one child owner')
    changes = {i:dict(owner=alias(owners[i]), status='pending', question=q['question'], reason='')
               for i,q in pending.items()}
    issues = []
    for q in refinement['new_issues']:
        require(isinstance(q, dict) and set(q) == {'owner','dimension','question'}, 'Invalid child question')
        issues.append({**q, 'owner':alias(q['owner'])})
    relations = []
    for r in refinement['relations']:
        require(isinstance(r, dict) and set(r) == {'source','target','kind'}, 'Invalid child relation')
        require(r['kind'] in ('applies_to', 'depends_on'),
                'Child relation kind must be applies_to or depends_on; shared subject is not a relation between outputs')
        relations.append(dict(source=alias(r['source']), target=alias(r['target']), kind=r['kind'], status='unresolved'))
    # commit uses the real answer's original issue ID for provenance; it does
    # not create a synthetic user answer or close any extra issue here.
    issue_id = next(a['event']['issue_id'] for a in reversed(interim['audit'])
                    if isinstance(a.get('event'), dict) and 'issue_id' in a['event'])
    result = commit(interim, dict(revision=interim['revision'], target=target, issue_id=issue_id,
                    answer=event.get('answer', {}), selected_columns=event.get('selected_columns', [])),
                    dict(children=children, issue_updates=changes, new_issues=issues, relations=relations,
                         retired_columns=[], retirement_reason='', coverage=refinement['coverage']), context)
    result['revision'] = state['revision'] + 1
    result['audit'].append(dict(action='answer_package', target=plan['target'], refinement_target=target,
                                interpretation=deepcopy(raw), review_parent=deepcopy(parent),
                                review_parts=deepcopy(children), review_relations=deepcopy(relations)))
    return validate(result)
