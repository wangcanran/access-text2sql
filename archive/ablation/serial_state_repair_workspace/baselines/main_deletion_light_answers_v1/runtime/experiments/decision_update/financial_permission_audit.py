"""Reproduce authorization checks from a saved financial trace, without an LLM."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from ...engine.execution import _authorized_graph, _connected, validate_sql_dependencies
from ...engine.simple_session import connected_options, menu_dependency_columns
from ...live import load_case
from ...storage import atomic_json


def audit(trace):
    record = json.loads(trace.read_text(encoding='utf8'))
    _, context, _, allowed, reference = load_case(113, 0)
    graph, error = _authorized_graph(context, allowed)
    # Locate by a literal schema signature, not natural-language similarity.
    matches = []
    for call in record['calls']:
        if call['stage'] != 'field_decision':
            continue
        decision = json.loads(call['response']['choices'][0]['message']['content'])
        if set(decision['scope_tables']) == {'account', 'card', 'loan'}:
            matches.append((call, decision))
    if len(matches) != 1:
        raise ValueError('Expected one saved account/card/loan field decision')
    call, decision = matches[0]
    payload = json.loads(call['messages'][-1]['content'])
    source = payload['source']
    target = {'item_id': source['id'], 'span': source['text'], **decision}
    # At this exact call only the current output and the unresolved prior-loan
    # output remain. Neither contributes a separate clear-node connectivity
    # constraint. Reconstruct only the inputs actually read by connected_options.
    session = {'allowed': allowed, 'analysis_mode': 'flat_compact',
               'view': {'items': {source['id']: target}}}
    variants = {}
    for name, scopes in [('recorded', decision['scope_tables']),
                         ('without_loan_anchor', ['account', 'card']),
                         ('client_via_disposition', ['disp', 'card'])]:
        projected = {**deepcopy(target), 'scope_tables': scopes}
        variants[name] = {'scope_tables': scopes,
            'authorized_leaves': [c['column'] for c in decision['field_candidates']
                if c['column'] in allowed and connected_options(session, context, projected, [c['column']])]}
    projected = {**deepcopy(target), 'menu_required_columns': menu_dependency_columns(
        {'required_columns': source['columns']}, decision, 'field')}
    variants['current_menu_dependencies'] = {
        'scope_tables': projected['scope_tables'],
        'common_required_columns': projected['menu_required_columns'],
        'authorized_leaves': [c['column'] for c in decision['field_candidates']
            if connected_options(session, context, projected, [c['column']])]}
    # This is a reduced request, NOT an equivalent implementation of the gold
    # request. The inaccessible loan predicate has been removed. A legal query
    # shows feasibility only; it does not establish that the user accepts it.
    reduced_sql = '''SELECT d.client_id, COUNT(DISTINCT c.card_id) AS card_count,
GROUP_CONCAT(DISTINCT c.type) AS card_types
FROM disp AS d JOIN card AS c ON d.disp_id = c.disp_id
GROUP BY d.client_id'''
    return {'source_trace': str(trace), 'question_id': 113, 'level': 0,
        'mode': 'offline_saved_state_audit', 'model_calls': 0,
        'allowed_columns': allowed,
        'original_query_check': validate_sql_dependencies(reference['sql'], context, allowed),
        'authorized_graph': {t: sorted(v) for t, v in graph.items()}, 'graph_error': error,
        'connectivity': {','.join(pair): _connected(graph, pair) for pair in
            [('client', 'card'), ('account', 'card'), ('disp', 'card')]},
        'saved_menu_decision': decision, 'candidate_scope_comparison': variants,
        'reduced_query_example': {'request': 'Return card counts and types by client identifier, without the loan condition.',
            'sql': reduced_sql, 'authorization_check': validate_sql_dependencies(reduced_sql, context, allowed),
            'equivalent_to_original': False, 'user_acceptance_tested': False,
            'database_execution_tested': False},
        'trace_result': {'outcome': record['outcome'], 'delivery': record['delivery'],
                         'actual_interactions': len(record['episode']['interactions'])}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.trace)
    atomic_json(args.out, result)
    print(json.dumps({'original_authorized': result['original_query_check']['sql_authorized'],
        'candidate_scope_comparison': result['candidate_scope_comparison'],
        'reduced_example_authorized': result['reduced_query_example']['authorization_check']['sql_authorized']},
        ensure_ascii=True, indent=2))
