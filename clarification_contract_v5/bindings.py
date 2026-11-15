"""A subject key is not evidence of a returned attribute binding."""
from copy import deepcopy


USES = {'returned', 'operand', 'condition', 'subject'}


def _compiled_use(role, kind, index, requested):
    """Compile model annotations into the small graph control vocabulary."""
    if role == 'modifier' and requested == 'returned':
        requested = None
    if requested in USES:
        return requested
    if role == 'output':
        if index == 0:
            return 'returned'
        return 'operand' if kind == 'calculation' else 'condition'
    if kind == 'relation':
        return 'subject'
    return 'operand' if kind == 'instruction' else 'condition'


def normalize_plan_bindings(nodes, context):
    """Canonicalize schema identifiers before any graph-specific inspection."""
    canonical = {t['name'] + '.' + c['name']: t['name'] + '.' + c['name']
                 for t in context['tables'] for c in t['columns']}
    folded = {name.casefold(): name for name in canonical}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if isinstance(node.get('columns'), list):
            node['columns'] = [folded.get(c.casefold(), c) if isinstance(c, str) else c
                               for c in node['columns']]
        claims = node.get('binding_claims', [])
        if isinstance(claims, list):
            for claim in claims:
                if isinstance(claim, dict) and isinstance(claim.get('columns'), list):
                    claim['columns'] = [folded.get(c.casefold(), c) if isinstance(c, str) else c
                                        for c in claim['columns']]
        if isinstance(node.get('candidates'), dict):
            normalized = {}
            for column, label in node['candidates'].items():
                if not isinstance(column, str):
                    raise ValueError('Candidate columns must be schema identifiers')
                normalized[folded.get(column.casefold(), column)] = label
            node['candidates'] = normalized
    return nodes


def validate_claims(claims, role, context, kind=None):
    columns = {t['name'] + '.' + c['name'] for t in context['tables'] for c in t['columns']}
    canonical = {c.casefold(): c for c in columns}
    if not isinstance(claims, list):
        raise ValueError('Every contribution needs explicit binding_claims')
    # A relation node can be a graph-level join/identity link rather than a
    # query contribution. Its columns remain permission checked from the node;
    # requiring a prose claim here duplicates the relation contract.
    if not claims and role == 'modifier' and kind == 'relation':
        return []
    if not claims:
        raise ValueError('Every business contribution needs explicit binding_claims')
    result = []
    for index, c in enumerate(claims):
        if (not isinstance(c, dict)
                or not isinstance(c.get('meaning'), str) or not c['meaning'].strip()
                or not isinstance(c.get('columns'), list)
                or any(not isinstance(x, str) or x.casefold() not in canonical for x in c['columns'])):
            raise ValueError('Invalid binding claim: meaning/use/state/real schema columns required')
        c = deepcopy(c)
        c['use'] = _compiled_use(role, kind, index, c.get('use'))
        c['state'] = c.get('state') if c.get('state') in ('open', 'bound') else (
            'bound' if c['columns'] else 'open')
        c['columns'] = list(dict.fromkeys(canonical[x.casefold()] for x in c['columns']))
        # Literal operands/conditions (for example ``top 3``, a threshold,
        # sort direction, or a date literal) are fully resolved without a
        # database column. Returned attributes and subject identities still
        # require real schema dependencies.
        if c['state'] == 'bound' and not c['columns'] and c['use'] not in ('operand', 'condition'):
            raise ValueError('A bound claim must name its real schema dependencies')
        if c['state'] == 'open' and c['use'] in ('returned', 'operand') and c['columns']:
            raise ValueError('An open returned/operand claim cannot commit alternative columns')
        result.append(c)
    if role == 'output' and not any(c['use'] == 'returned' for c in result):
        raise ValueError('An output needs a returned claim, not just a subject locator')
    if role == 'modifier' and any(c['use'] == 'returned' for c in result):
        raise ValueError('A modifier cannot acquire returned claims')
    return result


def promote_claims(row, role, context, column_key, kind=None):
    kind = kind or row.get('kind')
    default = [] if role == 'modifier' and kind == 'relation' else None
    claims = validate_claims(row.pop('binding_claims', default), role, context, kind)
    # Claims cannot evade authorization by declaring a column in metadata only.
    row[column_key] = list(dict.fromkeys(row.get(column_key, []) +
        [column for c in claims if c['state'] == 'bound' for column in c['columns']]))
    return claims


def closure_issues(session):
    issues = []
    view = session.get('view') or {}
    claims = session.get('binding_claims', {})
    for iid, n in view.get('items', {}).items():
        if n['role'] != 'output':
            continue
        returned = [c for c in claims.get(iid, []) if c['use'] == 'returned']
        if not returned:
            issues.append(dict(node=iid, code='output_without_return_binding'))
        elif any(c['state'] != 'bound' for c in returned):
            issues.append(dict(node=iid, code='open_output_binding'))
        committed = set(n['required_columns'])
        declared = {column for c in claims.get(iid, []) for column in c['columns']}
        if not declared <= committed:
            issues.append(dict(node=iid, code='uncommitted_binding_dependency'))
    return issues


def complete_open_question_claims(node, decisions):
    """Materialize unresolved claim records from code-owned pending decisions."""
    claims = deepcopy(node.get('binding_claims', []))
    questions = node.get('questions', {})
    if not isinstance(claims, list) or not isinstance(questions, dict):
        raise ValueError('Binding claims and questions must be structured objects')
    for decision in decisions:
        dimension = decision['dimension']
        question = questions.get(dimension)
        if not isinstance(question, str) or not question.strip():
            raise ValueError('Routed decision must retain its original dimension')
        purpose = decision['question']
        use = ('operand' if dimension == 'operation' else
               'returned' if dimension == 'field' and node['role'] == 'output' else
               'operand' if dimension == 'field' else 'condition')
        matching = [c for c in claims if c.get('meaning') == purpose]
        # Two ledger decisions can have the same wording but different dimensions.
        # Their claim types come from their decision identities, not the wording.
        shared_purpose = sum(d['question'] == purpose for d in decisions) > 1
        if not shared_purpose and any(c.get('use') != use for c in matching):
            raise ValueError('A pending decision cannot change its binding claim type')
        if any(c.get('state') == 'open' and c.get('use') == use for c in matching):
            continue
        claims.append(dict(meaning=purpose, use=use, state='open', columns=[]))
    node['binding_claims'] = claims
    return node


def route_unique_decision_dimensions(nodes, decisions):
    """Repair only structurally unique ledger routes within each old parent."""
    valid_ids = {d['decision_id'] for d in decisions}
    # Model-generated IDs that are absent from the current ledger are stale.
    # Removing the annotation cannot settle or delete anything; the ledger is
    # authoritative, and canonical_plan will route every still-pending ID.
    for node in nodes:
        if isinstance(node, dict) and isinstance(node.get('decision_ids'), list):
            node['decision_ids'] = [i for i in node['decision_ids'] if i in valid_ids]
    by_parent = {}
    for decision in decisions:
        by_parent.setdefault(decision['owner_id'], []).append(decision)
    for parent, old in by_parent.items():
        children = [n for n in nodes if n.get('parent') == parent]
        if not children or any(not isinstance(n.get('questions'), dict) for n in children):
            continue
        routes = {}
        for decision in old:
            candidates = [n for n in children if decision['dimension'] in n['questions']]
            if not candidates:
                holders = [n for n in children if decision['decision_id'] in n.get('decision_ids', [])]
                if len(holders) == 1:
                    holders[0]['questions'][decision['dimension']] = decision['question']
                    candidates = holders
            if len(candidates) != 1:
                break
            routes[decision['decision_id']] = candidates[0]
        else:
            old_ids = {d['decision_id'] for d in old}
            for node in children:
                node['decision_ids'] = [i for i in node.get('decision_ids', []) if i not in old_ids]
            for decision in old:
                routes[decision['decision_id']].setdefault('decision_ids', []).append(decision['decision_id'])
    return nodes


def derive_requires_operand_claims(nodes, edges):
    """Propagate explicit operand dependencies to instruction nodes."""
    by_id = {n.get('id'): n for n in nodes if isinstance(n, dict)
             and isinstance(n.get('id'), str)}
    for node in nodes:
        if (node.get('role') != 'modifier' or node.get('kind') != 'instruction'
                or node.get('binding_claims') or node.get('columns')):
            continue
        columns = set()
        for edge in edges:
            if (isinstance(edge, list) and len(edge) == 3 and edge[0] == node['id']
                    and edge[2] == 'requires' and edge[1] in by_id):
                target = by_id[edge[1]]
                for claim in target.get('binding_claims', []):
                    if (isinstance(claim, dict) and claim.get('use') == 'operand'
                            and claim.get('state') == 'bound'
                            and isinstance(claim.get('columns'), list)):
                        columns.update(c for c in claim['columns'] if isinstance(c, str))
        if columns:
            ordered = sorted(columns)
            node['columns'] = list(dict.fromkeys(node.get('columns', []) + ordered))
            node['binding_claims'] = [dict(meaning=node['text'], use='operand',
                                           state='bound', columns=ordered)]
    return nodes


def normalize_subject_edges(nodes, edges):
    """Subject providers are essential; normalize an optional-looking edge."""
    by_id = {n.get('id'): n for n in nodes if isinstance(n, dict)}
    result = []
    for edge in edges:
        if (isinstance(edge, list) and len(edge) == 3 and edge[2] == 'applies_to'
                and edge[0] in by_id and edge[1] in by_id
                and by_id[edge[0]].get('role') == 'modifier'
                and any(c.get('use') == 'subject' and c.get('state') == 'bound'
                        for c in by_id[edge[0]].get('binding_claims', []))):
            result.append([edge[1], edge[0], 'requires'])
        else:
            result.append(edge)
    return result


def validate_subject_edges(nodes, edges, parent_claims):
    """Require every output to retain each parent-level subject obligation.

    Subject claims describe the population of the whole compound parent.  A
    planner may choose either a direct edge or a chain through a required
    provider, but it may not silently omit the dependency: doing so would turn
    a denied subject into an unrestricted query.
    """
    by_id = {n.get('id'): n for n in nodes if isinstance(n, dict)}
    requires = {}
    for edge in edges:
        if (isinstance(edge, list) and len(edge) == 3
                and edge[2] == 'requires' and edge[0] in by_id
                and edge[1] in by_id):
            requires.setdefault(edge[0], set()).add(edge[1])
    for parent, claims in parent_claims.items():
        if not any(c.get('use') == 'subject' for c in claims):
            continue
        subject_leaves = {n['id'] for n in nodes
                          if n.get('parent') == parent
                          and n.get('kind') != 'relation'
                          and any(c.get('use') == 'subject'
                                  for c in n.get('binding_claims', []))}
        outputs = {n['id'] for n in nodes
                   if n.get('parent') == parent and n.get('role') == 'output'}
        if not subject_leaves:
            raise ValueError('Subject lineage has no subject provider for ' + parent)
        for output in outputs:
            seen = set()
            frontier = [output]
            while frontier:
                current = frontier.pop()
                if current in seen:
                    continue
                seen.add(current)
                frontier.extend(requires.get(current, ()))
            if not subject_leaves & seen:
                raise ValueError('Subject obligation is not connected to output ' + output)
    return nodes


def compile_subject_lineage(nodes, parent_claims):
    """Compile uniquely owned parent subject dependencies into child claims."""
    for parent, claims in parent_claims.items():
        subjects = [c for c in claims if c.get('use') == 'subject']
        if not subjects:
            continue
        children = [n for n in nodes if n.get('parent') == parent]
        relation_columns = {column for n in children if n.get('kind') == 'relation'
                            for column in n.get('columns', [])}
        essential = {column for c in subjects for column in c.get('columns', [])}
        distinguishing = essential - relation_columns
        for column in distinguishing:
            holders = [n for n in children if n.get('kind') != 'relation'
                       and column in n.get('columns', [])]
            if len(holders) != 1:
                raise ValueError('Subject lineage has no unique leaf for ' + parent + ': ' + column)
            holder = holders[0]
            matching = [c for c in holder.get('binding_claims', [])
                        if column in c.get('columns', [])]
            if len(matching) != 1 or holder.get('role') != 'modifier':
                raise ValueError('Subject lineage has no unique claim for ' + parent + ': ' + column)
            matching[0]['use'] = 'subject'
        if not any(n.get('kind') != 'relation'
                   and any(c.get('use') == 'subject' for c in n.get('binding_claims', []))
                   for n in children):
            raise ValueError('Subject lineage must survive as a non-relation subject leaf for ' + parent)
    return nodes




def remove_covered_summary_roots(raw, active_ids):
    """Discard only redundant planner summaries above real active children."""
    raw = deepcopy(raw)
    roots = [n for n in raw.get('nodes', []) if isinstance(n, dict)
             and n.get('parent') is None and n.get('id') in active_ids]
    for root in roots:
        children = [n for n in raw['nodes'] if n.get('parent') == root['id']]
        if not children or root.get('decision_ids'):
            continue
        child_columns = {c for n in children for c in n.get('columns', [])}
        if not set(root.get('columns', [])) <= child_columns:
            continue
        claims = root.get('binding_claims', [])
        child_claims = [c for n in children for c in n.get('binding_claims', [])]
        if any(not any(c.get('use') == claim.get('use') and
                       set(claim.get('columns', [])) <= set(c.get('columns', []))
                       for c in child_claims) for claim in claims):
            continue
        raw['nodes'].remove(root)
    return raw
