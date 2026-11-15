"""Compile contribution declarations into the existing typed dependency plan.

No role or edge direction is inferred from prose. The existing runtime remains
the sole authority for node allocation, questions, answers and deferred commit.
"""
from copy import deepcopy

def _subject_providers(nodes, parent_claims):
    """Resolve business obligations, never infer ownership from column overlap."""
    resolved = []
    for parent, claims in parent_claims.items():
        children = [n for n in nodes if n.get('parent') == parent]
        for obligation in claims:
            if obligation.get('use') != 'subject':
                continue
            meaning = obligation.get('meaning', '').strip()
            if not meaning:
                raise ValueError('Subject obligation requires a business meaning')
            matches = [(n, c) for n in children
                       if n.get('role') == 'modifier' and n.get('kind') != 'relation'
                       for c in n.get('binding_claims', [])
                       if c.get('meaning', '').strip() == meaning]
            if len(matches) != 1:
                raise ValueError('Subject lineage has no unique claim for ' + parent + ': ' + meaning)
            node, claim = matches[0]
            required = set(obligation.get('columns', []))
            if (not required <= set(claim.get('columns', []))
                    or not required <= set(node.get('columns', []))):
                raise ValueError('Subject provider lost dependencies for ' + parent + ': ' + meaning)
            resolved.append((parent, node, claim))
    return resolved


def compile_subject_lineage(nodes, parent_claims):
    """Compile subject use by preserved claim identity, allowing shared columns."""
    providers = _subject_providers(nodes, parent_claims)
    for _, _, claim in providers:
        claim['use'] = 'subject'
    return nodes


def validate_subject_edges(nodes, edges, parent_claims):
    """Every output must reach every inherited subject obligation provider."""
    requires = {}
    for source, target, kind in edges:
        if kind == 'requires':
            requires.setdefault(source, set()).add(target)
    providers = _subject_providers(nodes, parent_claims)
    for output in nodes:
        if output.get('role') != 'output':
            continue
        seen, frontier = set(), [output['id']]
        while frontier:
            current = frontier.pop()
            if current not in seen:
                seen.add(current)
                frontier.extend(requires.get(current, ()))
        for parent, provider, _ in providers:
            if output.get('parent') == parent and provider['id'] not in seen:
                raise ValueError('Subject obligation is not connected to output ' + output['id'])
    return nodes


def review_permission_facts(payload):
    """Expose exact code-computed authorization separately from schema presence."""
    result = deepcopy(payload)
    allowed = set(payload['allowed_columns'])
    result['permission_facts'] = [
        dict(id=node['id'], allowed_dependencies=sorted(set(node['columns']) & allowed),
             denied_dependencies=sorted(set(node['columns']) - allowed))
        for node in payload['canonical_nodes']]
    return result


def compile_plan(raw):
    if not isinstance(raw, dict) or set(raw) != {'nodes', 'retired_dependencies'}:
        raise ValueError('Expected nodes and retired_dependencies; code owns edges')
    if not isinstance(raw['nodes'], list) or not raw['nodes']:
        raise ValueError('Expected nonempty contribution list')
    result = deepcopy(raw)
    known = {}
    for n in result['nodes']:
        if not isinstance(n, dict) or not isinstance(n.get('id'), str) or not n['id']:
            raise ValueError('Each contribution requires a nonempty alias')
        if n['id'] in known:
            raise ValueError('Duplicate contribution alias')
        kind = n.get('semantic_class')
        if kind not in ('output', 'scope', 'requires', 'annotation'):
            raise ValueError('Explicit contribution semantic_class is required')
        if 'role' in n:
            raise ValueError('Code derives role from semantic_class')
        affects = n.get('affects')
        if (not isinstance(affects, list) or any(not isinstance(a, str) for a in affects)
                or len(affects) != len(set(affects))):
            raise ValueError('affects must contain distinct contribution aliases')
        known[n['id']] = n
    edges = []
    for n in result['nodes']:
        for consumer in n['affects']:
            if consumer not in known or consumer == n['id']:
                raise ValueError('Unknown or self contribution consumer')
            if n['semantic_class'] == 'annotation':
                edges.append([n['id'], consumer, 'applies_to'])
            else:
                edges.append([consumer, n['id'], 'requires'])
    # All modifiers must eventually contribute to an output. Reachability also
    # excludes disconnected cycles without forbidding valid shared providers.
    needed = {n['id'] for n in result['nodes'] if n['semantic_class'] == 'output'}
    while True:
        old = set(needed)
        needed.update(n['id'] for n in result['nodes'] if set(n['affects']) & needed)
        if needed == old:
            break
    if set(known) - needed:
        raise ValueError('Every modifier must contribute to a returned output')
    # A soft annotation cannot become a necessary provider through another
    # contribution; annotations may only decorate actual returned results.
    for n in result['nodes']:
        if n['semantic_class'] == 'annotation' and any(
                known[a]['semantic_class'] != 'output' for a in n['affects']):
            raise ValueError('Annotations may only decorate returned outputs')
        if n['semantic_class'] != 'annotation' and any(
                known[a]['semantic_class'] == 'annotation' for a in n['affects']):
            raise ValueError('Do not hide required contributions in annotations')
    for n in result['nodes']:
        n['role'] = 'output' if n.pop('semantic_class') == 'output' else 'modifier'
        n.pop('affects')
    result['edges'] = edges
    return result
