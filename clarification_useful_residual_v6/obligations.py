"""Persistent contribution commitments; questions remain owned by the ledger.

The pool never infers semantic equivalence from shared columns. Models identify
which old contributions their new claims refine; code owns IDs and lifecycle.
"""
from copy import deepcopy

VERSION = 'typed_obligation_pool_v1'
REFS = 'obligation_ids'

INITIAL_CONTRACT = '''
PERSISTENT CONTRIBUTION CONTRACT:
Enumerate ALL distinct requested returned information, subject restrictions,
predicates and formula operands as separate binding_claims, even inside a
compound item. Human-readable names and identity/join keys are different claims.
An explicitly known period predicate needs its date-column operand, not only
the year literal. Ground known contributions now, independently of other open
questions. Never bind all possible field candidates as required dependencies.
Only genuinely unknown business choices stay open. Code assigns contribution
IDs after validation. Initial claims do not supply obligation_ids.
Each initial binding_claim explicitly supplies use=returned|subject|condition|
operand and state=open|bound. Ranking, grouping and aggregation instructions
are operands; only actually requested returned information is returned.
For an open returned/operand claim, columns must be empty. If the business
operation is unresolved but a schema field is already known, state that field
as a separate bound operand claim and keep only the unresolved operation open.
Any node containing an independently requested returned result (including a
count or aggregate) MUST have role=output. modifier is only a non-returned
provider/condition. A returned claim on a modifier is a contract error, never
silently converted to a condition.
'''


def normalize_redundant_initial_columns(row):
    """Remove only open-claim columns already committed by bound claims.

    An open claim cannot establish a dependency. Repeated columns are safe to
    clear only when another bound claim on this same node already establishes
    every one of them; all other invalid open claims remain contract failures.
    """
    claims = row.get('binding_claims', [])
    if not isinstance(claims, list):
        return []
    bound_columns = set()
    for claim in claims:
        if (isinstance(claim, dict) and claim.get('state') == 'bound'
                and isinstance(claim.get('columns'), list)):
            bound_columns.update(c for c in claim['columns'] if isinstance(c, str))
    audit = []
    for claim in claims:
        if (not isinstance(claim, dict) or claim.get('state') != 'open'
                or claim.get('use') not in ('returned', 'operand')
                or not isinstance(claim.get('columns'), list) or not claim['columns']):
            continue
        columns = list(dict.fromkeys(claim['columns']))
        if set(columns) <= bound_columns:
            claim['columns'] = []
            audit.append({'meaning': claim.get('meaning', ''), 'columns': columns,
                          'rule': 'already_covered_by_bound_claim_same_node'})
    return audit


def normalize_unbound_initial_claims(row):
    """Safely downgrade unsupported bound output claims to open choices.

    This is opt-in for the semantic-compiler experiment. A model cannot prove
    a returned attribute without real schema columns; preserving it as an open
    claim lets the normal question ledger ask for the missing business choice.
    """
    claims = row.get('binding_claims', [])
    changed = []
    for claim in claims if isinstance(claims, list) else []:
        if (isinstance(claim, dict) and claim.get('state') == 'bound'
                and not claim.get('columns') and claim.get('use') in ('returned', 'subject')):
            claim['state'] = 'open'
            claim['columns'] = []
            meaning = str(claim.get('meaning', 'the requested attribute')).strip()
            questions = row.setdefault('questions', {})
            questions.setdefault('field', 'Which field or attribute should satisfy: ' + meaning + '?')
            changed.append(meaning)
    return changed

TRANSFER_CONTRACT = '''
PERSISTENT CONTRIBUTION TRANSFER (additional binding_claims field):
Every binding_claim in a replacement/decomposition child additionally has
"obligation_ids": ["o1"]. Use ONLY IDs carried by that child's own parent in
binding_claims/obligation_pool. This field identifies which existing business
contributions the claim preserves or refines; it does NOT choose lifecycle,
permissions, stable node IDs, or question IDs. New contributions use [].
Account for EVERY inherited contribution, including already-bound attributes,
conditions and subject constraints. A contribution may split across several
claims/children; carry its ID on each necessary part. Do not merge unrelated
contributions just because they use the same column. Preserve each contribution's
use (returned/subject/condition/operand) and its confirmed dependencies. A known
name output cannot become just an ID or count; a known time predicate keeps its
date operand. A user answer closes its asked question, not unrelated bindings.
Unanswered independent business choices remain open with their question IDs.
Never drop obligations yourself: code applies permission/NONE pruning AFTER
the complete split, and suspends obligations owned by blocked leaves.
For an inherited claim, code compiles its stable use from obligation_ids BEFORE
legacy validation; omit use or repeat the original exact use. Never attach an
old returned ID solely to an internal provider: preserve it on an output child
and add a distinct operand/provider claim with obligation_ids=[] and explicit
use=operand if needed. New claims explicitly supply use and state. All known
parent IDs must still be routed even when the same column is used elsewhere.
permission decisions are code-owned internal checks, not business questions;
they are resolved by authorization projection, not copied into questions.
For NONE, route the actually asked decision to its own contribution leaf.
Other field/value/operation questions about that SAME single contribution may
remain on the same leaf: loss of its field invalidates its value too. Split
independent contribution IDs before pruning; do not combine independent outputs
or conditions on the rejected leaf merely to reduce the number of nodes.
'''

GROUND = '''Resolve ONLY the supplied pending INTERNAL binding obligations.
Return JSON exactly {"resolutions":[{"claim_id":"b1","columns":["table.column"]}],
"unresolved":[]}. unresolved is an array of nonempty STRING reasons, never
objects. Failure example: {"resolutions":[],"unresolved":["b1: no supported binding"]}.
Cover every supplied claim_id exactly once, or return no
resolutions and nonempty unresolved reasons. Do not add
IDs, business values, formulas, questions, requirements, or deletion decisions.
The supplied current meanings and actual answers already specify the business
intent. Select all real schema operands needed to implement each meaning,
including date fields for periods and human-readable fields for name outputs.
Use the exact existing claim use; do not substitute entity locators for outputs.
Do not omit inaccessible fields: code checks permission afterwards. Do not
search alternative implementations. Do not use candidate fields as confirmed
choices when a business choice is still unknown. If binding requires an
unsupported business assumption, return resolutions=[] and specific reasons in
unresolved. Empty columns are allowed only for a fully known literal operand or
literal condition, never as a substitute for a database predicate or attribute.
'''


def _items(session):
    state = session.get('deferred_state') or {}
    return {**state.get('blocked_nodes', {}), **(session.get('view') or {}).get('items', {})}


def _refs(claim):
    ids = claim.get(REFS, [])
    if (not isinstance(ids, list) or any(not isinstance(i, str) for i in ids)
            or len(ids) != len(set(ids))):
        raise ValueError('obligation_ids must be distinct contribution IDs')
    return ids


def validate_role_claims(row, role):
    """Reject conflicting semantic declarations before legacy coercion."""
    claims = row.get('binding_claims', [])
    if isinstance(claims, list) and role == 'modifier' and any(
            isinstance(c, dict) and c.get('use') == 'returned' for c in claims):
        raise ValueError('A returned result, including count/aggregate, requires role=output; '
                         'do not silently convert returned claims to conditions')


def initialize(session):
    """Only a fresh, published inventory may seed commitments."""
    if 'obligation_pool' in session:
        return session
    if not session.get('view') or not isinstance(session.get('binding_claims'), dict):
        raise ValueError('Publish claims and inventory before initializing obligations')
    new = deepcopy(session)
    new['obligation_pool'] = {'version': VERSION, 'next_id': 1, 'contributions': {},
                              'questions': {}, 'relations': [], 'events': []}
    for owner in _items(new):
        for claim in new['binding_claims'].get(owner, []):
            if _refs(claim):
                raise ValueError('Initial contribution IDs are code-owned')
            _mint(new, owner, claim)
    return refresh(new, 'initialize')


def _mint(session, owner, claim):
    pool = session['obligation_pool']
    oid = 'o' + str(pool['next_id'])
    pool['next_id'] += 1
    pool['contributions'][oid] = dict(id=oid, origin_owner=owner,
        meaning=claim['meaning'], use=claim['use'], columns=list(claim['columns']),
        status='pending', holders=[])
    claim[REFS] = [oid]


def refresh(session, event='refresh'):
    """Project authoritative claims, ledger and typed edges into pool status.

    This must never mint replacement obligations for missing old commitments.
    """
    new = deepcopy(session)
    pool = new['obligation_pool']
    active = (new.get('view') or {}).get('items', {})
    blocked = (new.get('deferred_state') or {}).get('blocked_nodes', {})
    holders = {oid: [] for oid in pool['contributions']}
    for owner in _items(new):
        for index, claim in enumerate(new.get('binding_claims', {}).get(owner, [])):
            ids = _refs(claim)
            if not ids:
                raise ValueError('Every published claim needs a contribution obligation')
            for oid in ids:
                if oid not in holders:
                    raise ValueError('Unknown contribution obligation ' + oid)
                row = pool['contributions'][oid]
                if claim['use'] != row['use']:
                    raise ValueError('Contribution cannot change business use: ' + oid)
                holders[oid].append(dict(owner=owner, claim_index=index))
    for oid, row in pool['contributions'].items():
        current = holders[oid]
        if not current:
            raise ValueError('Contribution silently disappeared: ' + oid)
        old = row['status']
        row['holders'] = current
        eligible = [h for h in current if h['owner'] in active]
        if not eligible:
            row['status'] = 'pruned' if (new.get('deferred_state') or {}).get('committed') else 'blocked'
            if any(h['owner'] not in blocked for h in current):
                raise ValueError('Only code-blocked contributions can be suspended')
        else:
            ready = all(new['binding_claims'][h['owner']][h['claim_index']]['state'] == 'bound'
                        for h in eligible)
            row['status'] = 'ready' if ready else 'pending'
        if old != row['status']:
            pool['events'].append(dict(kind=event, obligation_id=oid, before=old, after=row['status']))
    # These are views of existing authorities, never a second mutable ledger.
    ledger = new['view']['progressive']['decision_ledger']
    current_ids = {d['decision_id'] for d in ledger}
    blocked_ids = {d['decision_id'] for d in (new.get('deferred_state') or {}).get('blocked_decisions', [])}
    blocked_ancestors = {d.get('ancestor_decision_id') for d in (new.get('deferred_state') or {}).get('blocked_decisions', [])
                         if isinstance(d.get('ancestor_decision_id'), str)}
    settled = {d['decision_id'] for d in new.get('settled_questions', [])}
    internal = {d['decision_id'] for d in new.get('internal_decision_audit', [])
                if d.get('effect') == 'internal_routed'}
    for did, row in pool['questions'].items():
        if did not in current_ids:
            if did in blocked_ids:
                row['status'] = 'blocked'
            elif did in blocked_ancestors and not any(
                    d.get('ancestor_decision_id') == did or d['decision_id'].startswith(did + '/')
                    for d in ledger):
                row['status'] = 'blocked'
            elif did in settled:
                row['status'] = 'answered'
            elif did in internal:
                row['status'] = 'internal_routed'
            elif row.get('status') in ('answered', 'blocked', 'transferred'):
                continue
            elif not any(d.get('ancestor_decision_id') == did or d['decision_id'].startswith(did + '/') for d in ledger):
                raise ValueError('Question disappeared without settlement or blocked owner: ' + did)
            else:
                row['status'] = 'transferred'
    for d in ledger:
        pool['questions'][d['decision_id']] = {**deepcopy(d), 'status': 'pending'}
    state = new.get('deferred_state') or {}
    pool['relations'] = deepcopy(state.get('canonical_typed_edges',
        state.get('canonical_edges', new['view'].get('relations', {}).get('edges', []))))
    return new


def transfer(before, after, replacements, *, retired=None, event='replace', project=True):
    """Validate total accounting at the same atomic boundary as tree updates."""
    new = deepcopy(after)
    new['obligation_pool'] = deepcopy(before['obligation_pool'])
    pool = new['obligation_pool']
    retired = retired or {}
    all_refs = {oid for p in replacements for c in before.get('binding_claims', {}).get(p, []) for oid in _refs(c)}
    for parent, children in replacements.items():
        old = before.get('binding_claims', {}).get(parent, [])
        inherited = {oid for c in old for oid in _refs(c)}
        if not children and inherited:
            raise ValueError('Deletion must first preserve and block a contribution leaf')
        claims = [(owner, c) for owner in children for c in new.get('binding_claims', {}).get(owner, [])]
        # Exact, unchanged claims need no semantic decision from a model.
        for owner, claim in claims:
            refs = _refs(claim)
            if not refs:
                matches = [c for c in old if c['meaning'] == claim['meaning'] and c['use'] == claim['use']
                           and set(c['columns']) <= set(claim['columns'])]
                if len(matches) == 1:
                    claim[REFS] = deepcopy(_refs(matches[0]))
            refs = _refs(claim)
            if not set(refs) <= inherited:
                raise ValueError('Contribution reference must stay within its own parent descendants')
            for oid in refs:
                if claim['use'] != pool['contributions'][oid]['use']:
                    raise ValueError('Contribution cannot change business use: ' + oid)
        # The state compiler must carry dependencies before the legacy
        # transaction derives node columns and authorization closure. This
        # boundary only verifies that the committed graph agrees with claims.
        for oid in inherited:
            required = {col for c in old if c.get('state') == 'bound' and oid in _refs(c)
                        for col in c['columns']}
            required -= set(retired.get(parent, []))
            holders = [c for _, c in claims if oid in _refs(c)]
            if not holders:
                raise ValueError('Contribution has no inheriting claim: ' + oid)
            carried = {col for c in holders for col in c['columns']}
            missing = required - carried
            if missing:
                raise ValueError('Inherited dependencies were not compiled before transaction: '
                                 + oid + ': ' + ', '.join(sorted(missing)))
        for oid in inherited:
            preserving = [c for _, c in claims if oid in _refs(c)]
            if not preserving:
                raise ValueError('Route every inherited contribution obligation: ' + oid)
            required = {col for c in old if c.get('state') == 'bound' and oid in _refs(c)
                        for col in c['columns']}
            actual = {col for c in preserving for col in c['columns']}
            if not required - set(retired.get(parent, [])) <= actual:
                raise ValueError('Contribution lost its confirmed dependencies: ' + oid)
        for owner, claim in claims:
            if not _refs(claim):
                _mint(new, owner, claim)
    # Unknown model IDs cannot be smuggled through an untouched node either.
    if not all_refs <= pool['contributions'].keys():
        raise ValueError('Pool has missing inherited identities')
    return refresh(new, event) if project else new


def issues(session):
    if 'obligation_pool' not in session:
        return [{'code': 'obligation_pool_not_initialized'}]
    try:
        fresh = refresh(session)
    except (ValueError, KeyError, TypeError) as exc:
        return [{'code': 'obligation_pool_inconsistent', 'detail': str(exc)}]
    if fresh['obligation_pool'] != session['obligation_pool']:
        return [{'code': 'obligation_pool_stale'}]
    problems = []
    for owner, node in fresh['view']['items'].items():
        if not isinstance(node.get('required_columns'), list):
            problems.append(dict(code='invalid_node_dependencies', node=owner))
            continue
        committed = set(node['required_columns'])
        declared = {column for claim in fresh['binding_claims'].get(owner, [])
                    if claim['state'] == 'bound' for column in claim['columns']}
        missing = declared - committed
        if missing:
            problems.append(dict(code='uncommitted_binding_dependency', node=owner,
                                 columns=sorted(missing)))
    problems.extend(dict(code='pending_internal_obligation', obligation_id=o['id'],
                         meaning=o['meaning'])
                    for o in fresh['obligation_pool']['contributions'].values()
                    if o['status'] == 'pending')
    return problems


def internal_tasks(session):
    """A business question owns its refinement; internal work never answers it."""
    waiting_owners = {d['owner_id'] for d in session['view']['progressive']['decision_ledger']}
    by_owner = {}
    for owner in session['view']['items']:
        if owner in waiting_owners:
            continue
        for index, claim in enumerate(session['binding_claims'].get(owner, [])):
            if claim['state'] != 'bound':
                by_owner.setdefault(owner, []).append(dict(claim_id='b' + str(index + 1),
                    claim_index=index, **deepcopy(claim)))
    return list(by_owner.items())


def payload(session, owners=None):
    owners = set(session['view']['items'] if owners is None else owners)
    return [deepcopy(o) for o in session['obligation_pool']['contributions'].values()
            if any(h['owner'] in owners for h in o['holders'])]
