"""Compile inherited declarations and internal authorization work once.

Question identities and contribution identities have independent lifecycles.
Permission probes are internal work, never unanswered business preferences.
"""
from copy import deepcopy

from .obligations import REFS, _refs, refresh


def canonicalize_model_edges(raw, base):
    """Remove edge copies that the canonical graph already owns.

    A transaction replaces parents with local aliases.  The model often emits
    both the retired parent edge and the alias edge.  Existing attachments are
    derived from the old graph by the transaction compiler, so accepting both
    forms makes correctness depend on response ordering.  New edges may still
    be declared when at least one endpoint is a local alias.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get('edges'), list):
        return raw
    view = base.get('view') if isinstance(base.get('view'), dict) else base
    if not isinstance(view, dict):
        return raw
    old_edges = view.get('relations', {}).get('edges', [])
    updates = raw.get('updates') or {}
    aliases = {child.get('id') for children in updates.values()
               for child in (children if isinstance(children, list) else [children])
               if isinstance(child, dict) and isinstance(child.get('id'), str)}
    replacements = {}
    for parent, children in updates.items():
        if isinstance(children, list) and len(children) == 1 and isinstance(children[0], dict):
            replacements[parent] = children[0].get('id')
        elif isinstance(children, dict):
            replacements[parent] = children.get('id')
    canonical = set()
    for edge in old_edges:
        try:
            source, target, kind = edge['source_id'], edge['target_id'], edge['kind']
        except (TypeError, KeyError):
            continue
        canonical.add((replacements.get(source, source), replacements.get(target, target), kind))
    kept = []
    for edge in raw['edges']:
        if not isinstance(edge, list) or len(edge) != 3:
            kept.append(edge)
            continue
        source, target, kind = edge
        normalized = (replacements.get(source, source),
                      replacements.get(target, target), kind)
        if normalized in canonical:
            continue
        # A boundary-to-boundary edge is not a model-owned edit.  If it is not
        # a canonical attachment, fail closed instead of letting the model
        # mutate a read-only part of the graph.
        if source not in aliases and target not in aliases:
            raise ValueError('New edge must involve a local replacement alias')
        kept.append(edge)
    result = deepcopy(raw)
    result['edges'] = kept
    return result


def compile_model_references(raw, session, base=None):
    """Compile every model reference at the transaction boundary.

    Answer payloads include read-only context, so a model can repeat a subject
    obligation from a neighboring modifier inside the edited output. The same
    ownership rule applies to pending decisions, question changes and edges:
    references to the current edit boundary are retained, read-only neighbor
    references are deduplicated, and unknown/cross-boundary references fail
    closed before the legacy transaction mutates anything.

    This is deliberately a compiler boundary rather than a field-specific
    sanitizer. Semantic text remains model-owned; identities and lifecycle are
    code-owned.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get('updates'), dict):
        raise ValueError('Model transaction must contain an updates object')
    result = deepcopy(raw)
    all_claims = session.get('binding_claims', {})
    owners = {}
    for owner, claims in all_claims.items():
        for claim in claims or []:
            for oid in _refs(claim):
                owners.setdefault(oid, set()).add(owner)
    authority = base if isinstance(base, dict) else session
    # `base` is a view snapshot in the frozen light runtime, while `session`
    # contains a view under `session['view']`.  Normalize that interface once
    # here; mixing the two shapes was the source of the false f2 boundary.
    view = (authority.get('view') if isinstance(authority.get('view'), dict)
            else authority if isinstance(authority.get('items'), dict) else {})
    progressive = view.get('progressive') or {}
    ledger = {d['decision_id']: d for d in
              (progressive.get('decision_ledger', []) or [])}
    editable = set(result['updates'])
    namespace = set(view.get('items', {}))
    aliases = set()
    for parent, children in result['updates'].items():
        if isinstance(children, dict):
            result['updates'][parent] = children = [children]
        if not isinstance(children, list):
            raise ValueError('Each update parent must contain a child array')
        for child in children:
            if isinstance(child, dict) and isinstance(child.get('id'), str):
                if child['id'] in aliases:
                    raise ValueError('Duplicate local child alias: ' + child['id'])
                aliases.add(child['id'])
        known = {oid for claim in all_claims.get(parent, []) for oid in _refs(claim)}
        for child in children or []:
            if not isinstance(child, dict):
                raise ValueError('Every replacement child must be an object')
            claims = child.get('binding_claims', []) if isinstance(child, dict) else []
            if claims is not None and not isinstance(claims, list):
                raise ValueError('binding_claims must be an array')
            kept = []
            for claim in claims:
                refs = _refs(claim)
                foreign = set(refs) - known
                external = {oid for oid in foreign if owners.get(oid, set()) - {parent}}
                if foreign and foreign == external and not (set(refs) & known):
                    # Pure duplicate of an untouched neighbor's contribution.
                    continue
                if foreign and external and set(refs) & known:
                    claim = deepcopy(claim)
                    claim[REFS] = [oid for oid in refs if oid not in external]
                kept.append(claim)
            child['binding_claims'] = kept
            pending = child.get('pending', [])
            if not isinstance(pending, list) or len(pending) != len(set(pending)):
                raise ValueError('pending must contain distinct decision IDs')
            if ledger:
                for decision_id in pending:
                    if decision_id not in ledger or ledger[decision_id]['owner_id'] != parent:
                        raise ValueError('Pending decision crosses the current parent boundary: ' + str(decision_id))

    changes = result.get('question_changes', {})
    if changes is not None:
        if not isinstance(changes, dict):
            raise ValueError('question_changes must be an object')
        if ledger:
            for decision_id in changes:
                if decision_id not in ledger:
                    raise ValueError('question_changes references an unknown decision: ' + str(decision_id))
                if ledger[decision_id]['owner_id'] not in editable:
                    raise ValueError('question_changes crosses the current edit boundary: ' + str(decision_id))

    edges = result.get('edges', [])
    if not isinstance(edges, list):
        raise ValueError('edges must be an array')
    current_ids = namespace
    for edge in edges:
        if not isinstance(edge, list) or len(edge) != 3 or any(not isinstance(v, str) for v in edge):
            raise ValueError('Each edge must be [source, target, kind]')
        source, target, _ = edge
        if source not in current_ids and source not in aliases and source not in editable:
            raise ValueError('Edge source is outside the current graph boundary: ' + source)
        if target not in current_ids and target not in aliases and target not in editable:
            raise ValueError('Edge target is outside the current graph boundary: ' + target)
    return canonicalize_model_edges(result, authority)


def sanitize_cross_parent_claims(raw, session):
    """Backward-compatible name for the unified reference compiler."""
    compiled = compile_model_references(raw, session)
    if isinstance(raw, dict):
        raw.clear()
        raw.update(compiled)
    return raw


def compile_inherited_claims(row, parent, session):
    """Resolve stable uses before legacy inference can reinterpret a claim."""
    claims = row.get('binding_claims')
    if not isinstance(claims, list):
        return row
    old = session.get('binding_claims', {}).get(parent, [])
    known = {oid: c['use'] for c in old for oid in _refs(c)}
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        refs = _refs(claim)
        if not refs:
            exact = [c for c in old if c['meaning'] == claim.get('meaning')
                     and (claim.get('use') is None or claim['use'] == c['use'])]
            if len(exact) == 1:
                refs = claim[REFS] = deepcopy(_refs(exact[0]))
        if refs:
            if not set(refs) <= known.keys():
                raise ValueError('Contribution references must belong to the current parent')
            uses = {known[oid] for oid in refs}
            if len(uses) != 1:
                raise ValueError('Different contribution uses require distinct claims')
            use = uses.pop()
            # An inherited type is code-owned. A redundant model annotation
            # cannot alter it or cause a spurious conflict; content/role checks
            # below still prevent a returned result becoming a modifier.
            claim['use'] = use
            inherited = [c for c in old if set(_refs(c)) & set(refs)]
            if 'state' not in claim and inherited and all(c['state'] == 'bound' for c in inherited):
                claim['state'] = 'bound'
            if row.get('role') == 'modifier' and use == 'returned':
                raise ValueError('Preserve returned obligation on an output child; '
                    'a separate internal operand/provider needs a new claim with obligation_ids=[]')
    # A business subject/condition with preserved contribution identity cannot
    # become a claim-free physical join merely due to a redundant kind label.
    if (row.get('role') == 'modifier' and row.get('kind') == 'relation'
            and any(c.get('use') in ('subject', 'condition') and _refs(c) for c in claims)):
        row['kind'] = 'attribute'
    if (row.get('role') == 'output' and claims
            and all(isinstance(c, dict) and c.get('use') in ('operand', 'condition', 'subject')
                    for c in claims)):
        raise ValueError('Node ' + str(row.get('id')) + ' is declared output but only carries '
            'non-returned contributions ' + str([(c.get('obligation_ids'), c['use']) for c in claims])
            + '. Preserve their inherited uses: an internal measure used only for ranking/filtering '
            'belongs on a requires/scope provider, not an invented returned result.')
    return row


def _compile_children_dependencies(parent, children, session, retired=()):
    old_claims = session.get('binding_claims', {}).get(parent, [])
    if not isinstance(children, list):
        raise ValueError('Replacement children must be an array')
    for child in children:
        if not isinstance(child, dict):
            raise ValueError('Every replacement child must be an object')
        compile_inherited_claims(child, parent, session)

    required = {}
    for claim in old_claims:
        if claim.get('state') != 'bound':
            continue
        for oid in _refs(claim):
            required.setdefault(oid, set()).update(claim.get('columns', []))
    for oid, columns in required.items():
        columns -= set(retired)
        holders = [claim for child in children for claim in child.get('binding_claims', [])
                   if oid in _refs(claim)]
        if not holders:
            continue
        carried = {column for claim in holders for column in claim.get('columns', [])}
        missing = columns - carried
        if missing:
            holders[0]['columns'] = list(dict.fromkeys(
                holders[0].get('columns', []) + sorted(missing)))
            if any(c.get('state') == 'bound' for c in old_claims if oid in _refs(c)):
                holders[0]['state'] = 'bound'

    for child in children:
        claims = child.get('binding_claims', [])
        child['columns'] = list(dict.fromkeys(child.get('columns', []) + [
            column for claim in claims if claim.get('state') == 'bound'
            for column in claim.get('columns', [])]))
    return children


def compile_inherited_dependencies(raw, session):
    """Restore inherited dependencies before the legacy transaction authorizes nodes."""
    if not isinstance(raw, dict) or not isinstance(raw.get('updates'), dict):
        raise ValueError('Transaction updates must be compiled before authorization')
    result = deepcopy(raw)
    retired = result.get('retire_columns') or {}
    if not isinstance(retired, dict):
        raise ValueError('Dependency retirement must be a parent-to-columns map')
    for parent, children in result['updates'].items():
        columns = retired.get(parent, [])
        if (not isinstance(columns, list) or any(not isinstance(c, str) for c in columns)
                or len(columns) != len(set(columns))):
            raise ValueError('Dependency retirement must contain distinct column names')
        _compile_children_dependencies(parent, children, session, columns)
    return result


def compile_plan_dependencies(nodes, session, retired_dependencies):
    """Compile plan claims after canonical validation approves retirements."""
    if not isinstance(nodes, list) or not isinstance(retired_dependencies, dict):
        raise ValueError('Canonical decomposition dependencies must be structured')
    by_parent = {}
    for node in nodes:
        by_parent.setdefault(node['parent'], []).append(node)
    retired = set(retired_dependencies)
    for parent, children in by_parent.items():
        old_required = set((((session.get('view') or {}).get('items') or {})
                            .get(parent, {}).get('required_columns') or []))
        _compile_children_dependencies(parent, children, session,
                                       retired & old_required)
        # The canonical graph validator checks the node-level dependency
        # inventory before the plan can be published.  A model may preserve an
        # inherited obligation in ``binding_claims`` but omit its physical
        # column from the node's ``columns`` (this is especially common after
        # a NONE split, where the claim is still open).  That omission is a
        # representational error, not a semantic retirement.  Recover it
        # deterministically from the old parent inventory and the obligation
        # owner, before canonical validation.  We never invent a dependency:
        # the column must already be required by the parent and the receiving
        # child must carry the same obligation reference (or be the sole
        # replacement child).  Ambiguous multi-child ownership remains
        # fail-closed.
        old_item = ((session.get('view') or {}).get('items') or {}).get(parent, {})
        inherited = set(old_item.get('required_columns') or [])
        inherited -= retired & set(old_item.get('required_columns') or [])
        declared = {c for child in children for c in (child.get('columns') or [])}
        missing = inherited - declared
        if missing:
            old_claims = (session.get('binding_claims') or {}).get(parent, [])
            old_by_oid = {}
            for claim in old_claims:
                for oid in _refs(claim):
                    old_by_oid.setdefault(oid, set()).update(claim.get('columns') or [])
            for column in sorted(missing):
                matching = []
                for child in children:
                    refs = set()
                    for claim in child.get('binding_claims') or []:
                        refs.update(_refs(claim))
                    if any(column in old_by_oid.get(oid, set()) for oid in refs):
                        matching.append(child)
                if not matching and len(children) == 1:
                    matching = [children[0]]
                if len(matching) != 1:
                    raise ValueError('Account for prior dependencies of ' + parent + ': ' + column)
                child = matching[0]
                child['columns'] = list(dict.fromkeys((child.get('columns') or []) + [column]))
                for claim in child.get('binding_claims') or []:
                    if any(column in old_by_oid.get(oid, set()) for oid in _refs(claim)):
                        claim['columns'] = list(dict.fromkeys((claim.get('columns') or []) + [column]))
    return nodes


def answer_review_context(session, event):
    """Capture the question and selected menu evidence relevant to this answer."""
    turn = event.get('turn', {})
    target = event.get('target')
    parents = (session.get('view') or {}).get('progressive', {}).get('parents', {})
    lineage, current = set(), target
    while isinstance(current, str) and current not in lineage:
        lineage.add(current)
        current = parents.get(current)
    history = []
    for previous in session.get('turns', []):
        if previous.get('target_id') not in lineage:
            continue
        history.append({key: deepcopy(previous.get(key)) for key in (
            'question', 'decision_ids', 'asked_dimension', 'requested_dimensions',
            'requested_slots', 'selected_options', 'answer') if key in previous})
    return dict(prior_interactions=history, asked_interaction={
        'question': deepcopy(event.get('question') or turn.get('question')),
        'decision_ids': deepcopy(turn.get('decision_ids', [])),
        'asked_dimension': event.get('dimension') or turn.get('asked_dimension'),
        'requested_dimensions': deepcopy(turn.get('requested_dimensions', [])),
        'requested_slots': deepcopy(turn.get('requested_slots', [])),
        'selected_options': deepcopy(turn.get('selected_options', [])),
        'answer': deepcopy(turn.get('answer', {}))})


def resolve_permission_probes(session):
    """Consume only internal permission decisions, retaining every contribution.

The actual authorization proof is produced by the following decomposition and
closure. A failed decomposition remains unresolved; this is not permission grant
or user answer, and it cannot authorize deletion on its own.
"""
    view = session['view']
    probes = [d for d in view['progressive']['decision_ledger'] if d['dimension'] == 'permission']
    if not probes:
        return session
    new = deepcopy(session)
    view = new['view']
    ids = {d['decision_id'] for d in probes}
    view['progressive']['decision_ledger'] = [d for d in view['progressive']['decision_ledger']
                                               if d['decision_id'] not in ids]
    new.setdefault('internal_decision_audit', []).extend(dict(
        decision_id=d['decision_id'], owner_id=d['owner_id'], kind='permission_projection',
        effect='internal_routed', evidence='awaiting_typed_decomposition') for d in probes)
    for node in view['items'].values():
        node['missing'] = [dim for dim in node.get('missing', []) if dim != 'permission']
        node.get('question_focus', {}).pop('permission', None)
    view['questions'] = [q for q in view.get('questions', [])
                         if q.get('dimension') != 'permission']
    view['segmentation']['inventory']['progressive'] = deepcopy(view['progressive'])
    return refresh(new, 'permission_projection')


REVIEW_COMMIT = '''
CODE-COMPILED DELETION TRANSACTION:
deletion_transaction identifies the exact proposed drop IDs, their recorded
closure proofs, candidate_action and remaining objective count. These are code
facts, not an invitation to search alternative implementations. Verify the
business scope and dependency meaning against the supplied history.
If candidate_action=refuse and no output survives, candidate_text MUST be empty;
this is valid refusal, not a missing deliverable. Do not reject solely for empty
text or demand another surviving query. Still reject genuinely overbroad
deletion, unsupported dependencies or disappearing independent outputs.
'''


def deletion_input(payload, session):
    result = deepcopy(payload)
    state = session['deferred_state']
    block_proofs = []
    blocked_ids = set(state['blocked_nodes'])
    for batch in state['batches']:
        aliases = batch.get('aliases', {})
        proofs = {}
        for owner, proof in batch['block_proofs'].items():
            stable_id = aliases.get(owner, owner)
            if stable_id not in blocked_ids:
                raise ValueError('Deletion proof has no blocked stable owner: ' + stable_id)
            if stable_id in proofs:
                raise ValueError('Duplicate deletion proof for stable owner: ' + stable_id)
            proofs[stable_id] = deepcopy(proof)
        block_proofs.append(dict(proofs=proofs))
    result['deletion_transaction'] = dict(
        drop_ids=sorted(state['blocked_nodes']),
        surviving_ids=sorted(session['view']['items']),
        remaining_objectives=sum(n['role'] == 'output' for n in session['view']['items'].values()),
        candidate_action='deliver' if any(n['role'] == 'output' for n in session['view']['items'].values()) else 'refuse',
        block_proofs=block_proofs)
    return result
