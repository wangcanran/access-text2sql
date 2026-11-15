"""Explicit ownership boundary for a single leaf's planning request."""
from copy import deepcopy
from .state import frontier, require


def payload(state, target, schema):
    leaves = frontier(state)
    require(target in leaves, 'Planning target must be an active leaf')
    def public(n):
        return {k: deepcopy(n.get(k, [])) for k in ('id', 'meaning', 'granularity', 'role', 'columns', 'scope_columns', 'computation', 'output_exclusions', 'answers')}
    editable = [deepcopy(q) for q in state['issues'].values()
                if q['owner'] == target and q['status'] == 'pending']
    # External issue IDs are not useful for assigning target children. Keep
    # their semantic context without exposing them as possible write handles.
    external_issues = [{k: deepcopy(q[k]) for k in ('owner', 'dimension', 'question', 'status')}
                       for q in state['issues'].values() if q['owner'] != target and q['status'] == 'pending']
    return dict(target=target, editable_leaf=public(leaves[target]), editable_issues=editable,
                allowed_issue_owner_ids=[q['id'] for q in editable],
                answered_local_decisions=[deepcopy(q) for q in state['issues'].values()
                                          if q['owner'] == target and q['status'] == 'answered'],
                read_only_context=dict(leaves=[public(n) for i, n in leaves.items() if i != target],
                    cancellation_events=[dict(issue_id=q['id'], cancelled_node_id=q['cancelled_node_id'],
                                              cancelled_meaning=state['nodes'][q['cancelled_node_id']]['meaning'],
                                              surviving_node_id=target)
                                         for q in editable if q.get('cancelled_node_id') in state['nodes']],
                    issues=external_issues, relations=deepcopy([r for r in state['relations'] if r['status'] != 'retired'])),
                schema=schema)
