"""Persistent refinement tree and derived frontier; no legacy session state."""
from copy import deepcopy

VERSION = 'clarification_tree_v6_explicit_scope'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def node(iid, meaning, parent=None):
    return dict(id=iid, parent_id=parent, children=[], lifecycle='active',
                source_text=meaning, meaning=meaning, role=None, granularity='compound',
                columns=[], scope_columns=[], computation={}, output_exclusions=[], answers=[], assessed=False)


def initial(request):
    require(text(request), 'Empty request')
    return dict(version=VERSION, revision=0, next_node=2, next_issue=1,
                original_request=request, nodes={'f1': node('f1', request)}, issues={}, relations=[], audit=[])


def frontier(state):
    return {i: n for i, n in state['nodes'].items()
            if not n['children'] and n['lifecycle'] == 'active'}


def graph(state):
    leaves = frontier(state)
    return {'nodes': deepcopy(list(leaves.values())), 'relations': deepcopy([
        r for r in state['relations'] if r['source'] in leaves and r['target'] in leaves
        and r['status'] != 'retired']), 'issues': deepcopy([
        q for q in state['issues'].values() if q['status'] == 'pending'])}


def add_issue(state, owner, dimension, question):
    require(owner in frontier(state) and text(question), 'Invalid issue owner/question')
    iid = 'd' + str(state['next_issue'])
    state['next_issue'] += 1
    state['issues'][iid] = dict(id=iid, owner=owner, dimension=dimension,
                               question=question, status='pending')
    return iid


def business_role(granularity, role=None):
    require(granularity in ('compound', 'atomic', 'undetermined'), 'Invalid granularity')
    if granularity != 'atomic':
        return None
    require(role in ('output', 'modifier'), 'Atomic leaf needs a business role')
    return role


def attachment_possible(source, target):
    # Compound leaves can contain conditions or outputs; no forced role labels.
    return (source['granularity'] != 'atomic' or source['role'] == 'modifier') and (
        target['granularity'] != 'atomic' or target['role'] == 'output')


def validate(state):
    nodes = state['nodes']
    require('f1' in nodes and nodes['f1']['parent_id'] is None, 'Missing root')
    require(nodes['f1']['source_text'] == state['original_request'], 'Root provenance changed')
    seen = set()
    def visit(iid):
        require(iid in nodes and iid not in seen, 'Tree cycle or repeated child')
        seen.add(iid)
        n = nodes[iid]
        require(n['id'] == iid, 'Node identity mismatch')
        require(isinstance(n.get('scope_columns', []), list), 'Invalid scope columns')
        require(n['role'] == business_role(n['granularity'], n['role']), 'Non-atomic node cannot have a business role')
        require(not n['children'] or n['lifecycle'] == 'active', 'Cancelled internal container')
        for c in n['children']:
            require(c in nodes and nodes[c]['parent_id'] == iid, 'Broken parent link')
            visit(c)
    visit('f1')
    require(seen == set(nodes), 'Detached tree node')
    leaves = frontier(state)
    for q in state['issues'].values():
        if q['status'] == 'pending':
            require(q['owner'] in leaves, 'Pending issue without active leaf owner')
    signatures = set()
    for r in state['relations']:
        if r['status'] == 'retired':
            continue
        require(r['source'] in leaves and r['target'] in leaves and r['source'] != r['target'],
                'Relation must connect distinct active leaves')
        require(r['kind'] in ('applies_to', 'depends_on') and
                r['status'] in ('confirmed', 'rejected', 'unresolved'), 'Invalid relation')
        if r['kind'] == 'applies_to':
            require(attachment_possible(leaves[r['source']], leaves[r['target']]),
                    f"Invalid attachment roles: applies_to {r['source']} "
                    f"({leaves[r['source']]['granularity']}/{leaves[r['source']]['role']}) -> "
                    f"{r['target']} ({leaves[r['target']]['granularity']}/{leaves[r['target']]['role']}); "
                    'an atomic source must be a modifier and an atomic target must be an output')
        key = (r['source'], r['target'], r['kind'])
        require(key not in signatures, 'Duplicate relation')
        signatures.add(key)
    return state
