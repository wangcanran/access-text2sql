"""Offline flow/provenance checks; semantic equivalence still needs trace review."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from clarification_deferred_deletion.baseline import module

VERSION = 'render_provenance_v1'


def _payload(call):
    payload = json.loads(call['messages'][1]['content'])
    return payload['input'] if call['stage'].endswith('_contract_retry') else payload


def _response(call):
    return json.loads(call['response']['choices'][0]['message']['content'])


def _nodes(nodes):
    return sorted((n.get('item_id', n.get('id')), n.get('role'), n.get('kind'),
                   n.get('requirement', n.get('text')), tuple(sorted(n.get('required_columns', n.get('columns', [])))))
                  for n in nodes)


def _edges(edges):
    return sorted(tuple(e[k] for k in ('source_id', 'target_id', 'kind'))
                  if isinstance(e, dict) else tuple(e) for e in edges)


def delivery_failures(record):
    failures = []
    session = record['episode']['session']
    delivery = record['delivery']
    text = delivery.get('text', '')
    view = session.get('view') or {}
    nodes = list(view.get('items', {}).values())
    edges = view.get('relations', {}).get('edges', [])
    def check(ok, reason):
        if not ok:
            failures.append(reason)
    check(isinstance(text, str) and bool(text.strip()), 'empty_delivery_text')
    check(not any(n.get('missing') or n.get('question_focus') for n in nodes), 'unresolved_node_delivered')
    ledger = view.get('progressive', {}).get('decision_ledger', [])
    check(not ledger, 'pending_decision_delivered')
    check(not any(session.get(k) for k in ('contract_unresolved_reason', 'deferred_incomplete',
          'deferred_review_rejection')), 'unresolved_contract_delivered')
    check(delivery == record['episode'].get('delivery'), 'episode_delivery_disagrees')
    mode = delivery.get('final_render')
    calls = record.get('calls', [])
    if mode == 'active_graph_model':
        rendered = [c for c in calls if c['stage'].removesuffix('_contract_retry') == 'final_active_request']
        check(bool(rendered), 'render_evidence_missing')
        if rendered:
            call = rendered[-1]
            payload = _payload(call)
            supplied = payload.get('nodes', payload.get('returned_values', []) + payload.get('nonreturned_requirements', []))
            check(_nodes(supplied) == _nodes(nodes), 'render_graph_nodes_disagree')
            check(_edges(payload.get('edges', [])) == _edges(edges), 'render_graph_edges_disagree')
            check(all(not n.get('missing') for n in supplied), 'unresolved_render_input')
            raw = _response(call)
            check(isinstance(raw, dict) and set(raw) == {'request'} and
                  isinstance(raw['request'], str) and raw['request'].strip() == text,
                  'render_output_disagrees')
    elif mode == 'single_active_requirement':
        check(len(nodes) == 1 and nodes[0].get('role') == 'output' and
              text == nodes[0].get('requirement'), 'single_requirement_disagrees')
        check(not session.get('needs_final_render'), 'required_render_skipped')
    elif mode is None:
        check(text == session.get('current_request'), 'delivery_text_disagrees')
    else:
        check(False, 'unknown_render_mode')
    return failures


def commit_failures(record):
    failures = []
    session = record['episode']['session']
    delivery = record['delivery']
    nodes = list((session.get('view') or {}).get('items', {}).values())
    edges = (session.get('view') or {}).get('relations', {}).get('edges', [])
    calls = record.get('calls', [])
    text = delivery.get('text', '')
    def check(ok, reason):
        if not ok:
            failures.append(reason)
    state = session.get('deferred_state')
    if state:
        check(state.get('committed') is True, 'deferred_not_committed')
        check(session.get('deferred_final') == delivery, 'committed_delivery_disagrees')
        reviews = [c for c in calls if c['stage'].removesuffix('_contract_retry') == 'deferred_commit_review']
        check(bool(reviews), 'commit_review_missing')
        if reviews:
            payload, raw = _payload(reviews[-1]), _response(reviews[-1])
            check(payload.get('candidate_text') == text, 'review_candidate_disagrees')
            check(_nodes(payload.get('surviving_nodes', [])) == _nodes(nodes), 'review_graph_nodes_disagree')
            check(_edges(payload.get('surviving_edges', [])) == _edges(edges), 'review_graph_edges_disagree')
            check(raw == {'accepted': True, 'issues': []}, 'commit_review_not_accepted')
    return failures


def inspect_case(record, expected_sources):
    # Keep every original permission, menu-domain, isolation and flow check.
    result = module('acceptance').inspect_case(record, expected_sources)
    result['legacy_failures'] = list(result['failures'])
    if record.get('outcome') == 'deliver':
        result['failures'] = [f for f in result['failures'] if f != 'delivery_text_disagrees']
        try:
            result['failures'].extend(delivery_failures(record))
        except (KeyError, TypeError, ValueError, IndexError):
            result['failures'].append('invalid_delivery_evidence')
    if record.get('outcome') in ('deliver', 'refuse'):
        if record.get('config', {}).get('method', '').startswith('useful_residual_v6_pool_v'):
            from .obligations import issues
            result['failures'].extend(i['code'] for i in issues(record['episode']['session']))
            if any(c['stage'].removesuffix('_contract_retry') == 'binding_completion'
                   for c in record.get('calls', [])):
                result['failures'].append('pool_called_global_completion_audit')
        try:
            result['failures'].extend(commit_failures(record))
        except (KeyError, TypeError, ValueError, IndexError):
            result['failures'].append('invalid_commit_evidence')
    if record.get('overlay_unchanged') is not True:
        result['failures'].append('overlay_sources_not_verified')
    result['failures'] = sorted(set(result['failures']))
    result['acceptance_contract'] = VERSION
    return result


def acceptance_matrix(expected, observed, snapshots_ok, rows):
    missing = sorted(set(expected) - set(observed))
    passed = (set(observed) == set(expected) and len(observed) == len(expected)
              and snapshots_ok and all(not row['failures'] for row in rows))
    return passed, missing


def main(argv=None):
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    cfg = json.loads((args.folder/'CONFIG.json').read_text(encoding='utf8'))
    expected = {tuple(pair) for pair in cfg['cases']}
    rows, observed = [], []
    for path in sorted(args.folder.glob('*_*_reduction.json')):
        record = json.loads(path.read_text(encoding='utf8'))
        observed.append((record['question_id'], record['level']))
        rows.append(inspect_case(record, cfg['runtime_source_sha256']))
    source_groups = [('sources', cfg['runtime_source_sha256'])]
    for key, folder in [('overlay_v2_sha256','v2_sources'), ('overlay_v4_sha256','v4_1_sources'),
                        ('overlay_v5_sha256','v5_sources'), ('integration_sha256','integration_sources'),
                        ('method_source_sha256','method_sources')]:
        if key in cfg:
            source_groups.append((folder, cfg[key]))
    snapshots_ok = all((args.folder/folder/name).is_file() and
                       hashlib.sha256((args.folder/folder/name).read_bytes()).hexdigest() == sha
                       for folder, hashes in source_groups for name, sha in hashes.items())
    passed, missing = acceptance_matrix(expected, observed, snapshots_ok, rows)
    report = dict(acceptance_contract=VERSION,
                  acceptance_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  control_flow_acceptance=passed,
                  expected_cases=len(expected), observed_cases=len(rows),
                  missing_cases=missing, source_snapshots_match_manifest=snapshots_ok,
                  outcomes=dict(Counter(r['outcome'] for r in rows)), cases=rows,
                  semantic_quality='Not proved: inspect purpose alignment and residual meaning separately.',
                  sql_execution='Not performed.')
    module('storage').atomic_json(args.out or args.folder/'ACCEPTANCE_V6.json', report)
    print(json.dumps({k:v for k,v in report.items() if k != 'cases'}, ensure_ascii=False))
    for row in rows:
        print(row['case'], row['failures'])
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
