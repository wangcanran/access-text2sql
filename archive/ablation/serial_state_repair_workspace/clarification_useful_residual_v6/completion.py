"""Internal grounding completion, separate from user-question settlement."""
from copy import deepcopy
import hashlib
import json

from clarification_contract_v5.bindings import validate_claims


COMPLETE = '''Complete the INTERNAL schema grounding and output-expression audit.
The user-question ledger is closed. Do NOT ask the user to repeat known facts.
Return JSON only: {"nodes":[{"id":"existing ID", "requirement":"complete current
business requirement", "binding_claims":[{"meaning":"one contribution",
"use":"returned|subject|operand|condition", "state":"bound",
"columns":["table.column"]}]}], "unresolved":[]}.
Cover every supplied active node exactly once, retaining IDs, roles and ALL
current outputs, conditions, operands and actual answers. Never revive blocked
requirements from the original request; it supplies context only. No SQL.
Check requested display attributes separately from identity/join keys. A request
to name entities requires human-readable names, not only IDs; listing entities
does not authorize replacing them with a count. Preserve distinct requested
attributes as distinct returned claims. Explain formulas using their operands.
Ground every known predicate, period, aggregate and cross-table relationship:
e.g. "in 2012" needs its date operand, not only the literal 2012; a cross-table
lookup needs its join columns. Literal-only claims (top 3, sort direction) may
have no columns; they cannot stand in for an ungrounded database predicate.
Retain all supplied required columns and all existing claim dependencies.
Do not omit dependencies because they are unauthorized. Code checks permissions
AFTER this completion and applies the existing deletion mechanism; do not look
for alternative implementations. Do not invent business values or settle new
business ambiguities. If the supplied evidence cannot support completion, put
specific reasons in unresolved and leave nodes empty. Otherwise unresolved=[].
'''


def completion_input(session, context, schema):
    return {'original_request': session.get('original_request', ''),
            'nodes': [{**deepcopy(n), 'binding_claims': deepcopy(session.get('binding_claims', {}).get(i, []))}
                      for i, n in session['view']['items'].items()],
            'edges': deepcopy(session['view'].get('relations', {}).get('edges', [])),
            'actual_answers': deepcopy(session.get('turns', [])),
            'schema': schema}


def completion_signature(session):
    data = {'nodes': session['view']['items'], 'claims': session.get('binding_claims', {}),
            'edges': session['view'].get('relations', {}).get('edges', []),
            'turns': session.get('turns', [])}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_completion(raw, session, context):
    if (not isinstance(raw, dict) or set(raw) != {'nodes', 'unresolved'}
            or not isinstance(raw['nodes'], list) or not isinstance(raw['unresolved'], list)
            or any(not isinstance(s, str) or not s.strip() for s in raw['unresolved'])):
        raise ValueError('Expected nodes array and unresolved reason strings')
    if raw['unresolved']:
        if raw['nodes']:
            raise ValueError('Unresolved completion must not partially update nodes')
        return deepcopy(raw)
    old = session['view']['items']
    seen = set()
    result = []
    for row in raw['nodes']:
        if not isinstance(row, dict) or set(row) != {'id', 'requirement', 'binding_claims'}:
            raise ValueError('Completion rows require exactly id, requirement, binding_claims')
        iid = row['id']
        if not isinstance(iid, str) or iid not in old or iid in seen:
            raise ValueError('Completion cannot add/remove/duplicate node identities')
        seen.add(iid)
        if not isinstance(row['requirement'], str) or not row['requirement'].strip():
            raise ValueError('Completion must retain the business requirement')
        claims = validate_claims(row['binding_claims'], old[iid]['role'], context, old[iid]['kind'])
        if any(c['state'] != 'bound' for c in claims):
            raise ValueError('Every claim use must close; internal open conditions cannot be delivered')
        columns = {c for claim in claims for c in claim['columns']}
        previous = set(old[iid]['required_columns']) | {
            c for claim in session.get('binding_claims', {}).get(iid, []) for c in claim['columns']}
        if not previous <= columns:
            raise ValueError('Completion cannot retire previously declared dependencies')
        result.append(dict(id=iid, requirement=row['requirement'], binding_claims=claims,
                           columns=sorted(columns)))
    if seen != set(old):
        raise ValueError('Completion must cover every active node')
    return {'nodes': result, 'unresolved': []}


def readiness_issues(session):
    issues = []
    for iid, node in (session.get('view') or {}).get('items', {}).items():
        for claim in session.get('binding_claims', {}).get(iid, []):
            if claim['state'] != 'bound':
                issues.append({'node': iid, 'code': 'open_internal_binding', 'meaning': claim['meaning']})
            if not set(claim['columns']) <= set(node['required_columns']):
                issues.append({'node': iid, 'code': 'uncommitted_binding_dependency'})
    return issues
