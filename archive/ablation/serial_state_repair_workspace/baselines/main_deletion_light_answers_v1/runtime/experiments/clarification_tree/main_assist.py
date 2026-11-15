"""Localized tree guidance, inspired by the main method; no runtime import of it.

Module name retained for callers. The tree owns its prompts and contracts.
"""
from copy import deepcopy

from .state import frontier, graph


def refinement_rules():
    return '''LOCAL TREE REFINEMENT:
Split a fragment only when the actual answer makes independent treatment useful.
Keep calculations with operands unless the event reveals independent requirements.
Preserve AND/OR, negation, shared conditions and every unanswered remainder.
Edit only the target leaf; other leaves are read-only context. Parent identity
is provenance, not business role or automatic answer inheritance. Assign selected
fields to their owning contribution only. Relationships are proposals until
confirmed. No full-request reconstruction, exact quotes or legacy item envelopes.
Granularity controls the representation: compound/undetermined nodes have no
business role. Only atomic leaves declare output/modifier. An applies_to edge may
connect compound leaves; later expansion reopens its child scope. Its direction
is condition -> affected contribution, independent of whether either is atomic.
Compound remainders may carry inherited or explicitly selected dependencies,
but cannot introduce a catalogue of speculative fields. Bind a concrete local
contribution when it is materialized. Do not relabel a compound node atomic just
to bind all its fields or satisfy a relationship check.
'''


def context(state, target):
    """Current leaves plus explicit answered/pending identities; no ancestor text."""
    view = graph(state)
    for n in view['nodes']:
        n.pop('source_text', None)
    current = deepcopy(frontier(state)[target])
    current.pop('source_text', None)
    return {'target': current, 'current_graph': view,
            'local_decisions': deepcopy([q for q in state['issues'].values()
                                        if q['owner'] == target]),
            'helper': 'localized_tree_refinement_v3'}


def update_prompt(base):
    return refinement_rules() + '\nTREE TRANSACTION CONTRACT (authoritative):\n' + base + '''
After this answer, identify which contributions now need independent treatment.
For example, a confirmed shared subject may be separated from a still-compound
set of requested results. Preserve that remainder as one compound child rather
than requiring a complete decomposition. Do not force expansion for a simple
parameter update. coverage must explain why revising one leaf suffices OR why
these children need independent treatment, and where every remainder survives.
'''


def plan_prompt(base):
    return base + '''
LOCAL TREE GUIDANCE: parent identity is provenance, not answer
inheritance; a compound leaf is a container, not evidence that its whole meaning
is already clear. local_decisions explicitly lists answered and pending IDs.
Do not re-ask an answered business choice in schema-implementation wording.
An account-owner business answer is not a request to reconfirm its join mapping.
Describe only the new information still needed. Reuse existing pending identity
with optional issue_id; paraphrasing is not a new issue. Never reopen answered IDs.
Boundary and relation choices confirm meaning regardless of implementation
availability. Keep columns=[] in their options; map accepted meaning later.
Field options are executable candidate bundles and require column dependencies.
Ask ONE concrete gap about a named contribution, such as the ratio formula or
which credit-card information is needed. Do not ask the user to restate the whole
profile or clarify all remaining contributions. needs_local_focus means choose
such a specific question, not a synthetic catch-all question. Compound nodes
omit role (or use null) and retain their existing columns only; do not guess all
columns for the entire remainder. Atomic leaves alone declare a business role.
'''
