"""Offline acceptance checks for a completed fixed-version clarification matrix.

Checks recorded control-flow and dependency invariants. It deliberately does not
infer semantic equivalence or SQL execution success from NL delivery.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .storage import atomic_json
from .saved_calls import resolve_saved_call
from .objective_contract import has_query_objective

from .runtime import ROOT


def inspect_case(record, expected_sources):
    failures = []
    episode = record.get('episode', {})
    session = episode.get('session', {})
    delivery = record.get('delivery', {})
    allowed = set(record.get('allowed_columns', []))
    outcome = record.get('outcome', 'unfinished')
    def check(condition, reason):
        if not condition:
            failures.append(reason)
    # A matrix worker may be interrupted or exhaust its configured model-call
    # budget. That is an incomplete case, not a successful refusal.
    check(outcome in ('deliver', 'refuse'), 'episode_not_completed')
    check(record.get('frozen_unchanged') is True, 'frozen_sources_not_verified')
    check(record.get('runtime_unchanged') is True and record.get('runtime_source_sha256') == expected_sources,
          'runtime_sources_not_verified')
    if outcome == 'deliver':
        check(delivery.get('available_for_text2sql') is True, 'delivery_flag_disagrees')
        check(bool(delivery.get('text', '').strip()) and delivery['text'] == session.get('current_request'),
              'delivery_text_disagrees')
        check(bool(session.get('view')) and not session.get('update_base'), 'unfinished_analysis_delivered')
        check(has_query_objective((session.get('view') or {}).get('items', {}).values()),
              'delivery_without_query_objective')
        check(not any(session.get(k) for k in ('pending', 'menu_frontiers', 'permission_issues',
                  'lineage_pending', 'menu_blockers', 'removal_no_query')), 'unresolved_state_delivered')
        dependencies = {c for n in (session.get('view') or {}).get('items', {}).values()
                        for c in n['required_columns']}
        check(dependencies <= allowed, 'declared_forbidden_dependency_delivered')
        check(delivery.get('execution_authorized') is False and delivery.get('requires_sql_permission_check') is True,
              'nl_handoff_misrepresented_as_execution')
    elif outcome == 'refuse':
        check(delivery.get('reason') == 'no_remaining_query_objective', 'unexpected_business_refusal')
        check(session.get('removal_no_query') is True and not session.get('current_request'),
              'nonempty_request_refused_as_empty')
    if outcome != 'deliver':
        check(delivery.get('available_for_text2sql', False) is False, 'non_delivery_marked_available')

    for state in episode.get('snapshots', []) + [session]:
        ledger = state.get('lineage_pending', {})
        for key, evidence in ledger.items():
            check(evidence.get('evidence_id') == key, 'pending_identity_disagrees')
            check(len(evidence['issue'].get('denied_columns', [])) == 1, 'pending_record_not_single_column')
        for turn in state.get('turns', []):
            for option in turn.get('selected_options', []):
                check(set(option['required_columns']) <= allowed, 'accepted_leaf_contains_forbidden_column')

    # Inputs to category/leaf rendering are AFTER code permission filtering.
    # This checks these recorded domains, not whether every model label is a
    # semantically accurate description, nor unrecorded scope-menu bindings.
    rendered_choices = 0
    for call in record.get('calls', []):
        if 'reused_from' in call:
            try:
                call = resolve_saved_call(call, ROOT, model=record.get('model'))
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                check(False, 'saved_response_reference_not_verified')
                continue
            check(bool(call.get('messages')), 'saved_response_input_missing')
        if not call.get('messages'):
            continue
        payload = json.loads(call['messages'][1]['content'])
        if call['stage'].endswith('_contract_retry'):
            payload = payload['input']
        stage = call['stage'].removesuffix('_contract_retry')
        if stage in ('hierarchical_field_labels', 'hierarchical_menu_groups'):
            for choice in payload['choices']:
                rendered_choices += 1
                check({f['column'] for f in choice['fields']} <= allowed,
                      'forbidden_column_in_rendered_domain')
        if stage != 'session_user_gold':
            check('private_gold_sql' not in payload, 'gold_sql_outside_simulator')

    public = episode.get('interactions', [])
    return {'case': f"{record['question_id']}_{record['level']}", 'outcome': outcome,
        'reason': delivery.get('reason') or record.get('error') or record.get('stop_reason'),
        'interactions': len(public), 'effective_turns': len(session.get('turns', [])),
        'deletions': len(session.get('reduction_audit', [])),
        'resynchronizations': len(session.get('lineage_resync_audit', [])),
        'remaining_pending': len(session.get('lineage_pending', {})),
        'delivery': delivery.get('text', ''), 'cost': record.get('actual_cost'),
        'rendered_choices_checked': rendered_choices,
        'stage_counts': dict(Counter(c['stage'] for c in record.get('calls', []))),
        'failures': sorted(set(failures))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('matrix', type=Path)
    args = parser.parse_args(argv)
    folder = args.matrix
    manifest = json.loads((folder / 'CONFIG.json').read_text(encoding='utf8'))
    expected = {(i, l) for i in map(int, manifest['ids'].split(','))
                for l in map(int, manifest['levels'].split(','))}
    rows = []
    found = set()
    for path in sorted(folder.glob('*_*_reduction.json')):
        record = json.loads(path.read_text(encoding='utf8'))
        found.add((record['question_id'], record['level']))
        rows.append(inspect_case(record, manifest['runtime_source_sha256']))
    snapshots_match = all((folder / name).is_file() and
        hashlib.sha256((folder / name).read_bytes()).hexdigest() == sha
        for name, sha in manifest['runtime_source_sha256'].items())
    result = {'expected_cases': len(expected), 'observed_cases': len(rows),
        'missing_cases': sorted(expected - found), 'unexpected_cases': sorted(found - expected),
        'source_snapshots_match_manifest': snapshots_match,
        'control_flow_acceptance': found == expected and len(rows) == len(expected) and snapshots_match
            and all(not row['failures'] for row in rows),
        'outcomes': dict(Counter(row['outcome'] for row in rows)), 'cases': rows,
        'semantic_quality': 'Requires separate trace review; not proved by these checks.',
        'sql_execution': 'Not performed by this clarification runner.'}
    atomic_json(folder / 'ACCEPTANCE.json', result)
    print(json.dumps({k: v for k, v in result.items() if k != 'cases'}, ensure_ascii=False))
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    if not result['control_flow_acceptance']:
        raise SystemExit(1)
    return result


if __name__ == '__main__':
    main()
