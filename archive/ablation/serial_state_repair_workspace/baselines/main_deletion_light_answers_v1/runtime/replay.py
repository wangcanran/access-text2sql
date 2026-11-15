"""Exact saved-call replay through locally migrated sources; no network."""
from copy import deepcopy
import json
from .runtime import ROOT, engine_module as frozen_module
from .method import MainMethod, METHOD_VERSION, CLARITY_VERSION, EVIDENCE_VERSION as VERSION, VERSION as PROGRESSIVE_VERSION
from .response_boundary import decode_response, ResponseBoundaryError
from .saved_calls import resolve_saved_call

def saved_response(entry):
    """Resolve Recorder cache references without issuing a model request."""
    return resolve_saved_call(entry, ROOT)



def consume_response(calls, position, stage, prompt, payload):
    """Replay recorded bounded response retries inside one logical model call."""
    previous = None
    used = {'response': 0, 'transport': 0}
    while True:
        if position == len(calls):
            raise AssertionError('Replay requested an unrecorded call: ' + stage)
        saved = saved_response(calls[position])
        position += 1
        assert stage == saved['stage'], (position, stage, saved['stage'])
        assert prompt == saved['messages'][0]['content'], ('Prompt changed', position, stage)
        assert payload == json.loads(saved['messages'][1]['content']), ('Payload changed', position, stage)
        if previous is not None:
            assert saved.get('response_attempt') == previous['response_attempt'] + 1, 'Retry attempt discontinuity'
            for k in ('input_signature', 'request_options', 'retry_limits'):
                assert saved.get(k) == previous.get(k), 'Retry input/options changed'
        limits = saved.get('retry_limits', {'response': 2, 'transport': 0})
        assert set(limits) == {'response', 'transport'} and all(type(v) is int and 0 <= v <= 2 for v in limits.values())
        if saved.get('response_status') == 'transport_failure':
            assert saved.get('failure_layer') == 'transport_or_sdk'
            assert saved.get('transport_kind') in ('connection_or_timeout', 'http_408', 'http_429',
                                                  'http_500', 'http_502', 'http_503', 'http_504')
            assert saved.get('retry_scheduled') is True, 'Cannot skip a terminal transport failure'
            used['transport'] += 1
            assert used['transport'] <= limits['transport'], 'Transport retry limit exceeded'
            assert isinstance(saved.get('response_attempt'), int) and 1 <= saved['response_attempt'] <= sum(limits.values())
            previous = saved
            continue
        try:
            return deepcopy(decode_response(saved['response'])), position
        except ResponseBoundaryError as exc:
            if not (exc.retryable and saved.get('retry_scheduled') is True):
                raise
            assert saved.get('failure_layer') == 'response_boundary' and saved.get('response_status') == exc.kind
            used['response'] += 1
            assert used['response'] <= limits['response'], 'Response retry limit exceeded'
            assert isinstance(saved.get('response_attempt'), int) and 1 <= saved['response_attempt'] <= sum(limits.values())
            previous = saved



def replay(record, context, units, *, deterministic_empty_relations=False, integrated_method=False):
    calls = record['calls']
    position = 0
    def call(stage, prompt, payload):
        nonlocal position
        result, position = consume_response(calls, position, stage, prompt, payload)
        return result
    skipped = []
    config = record.get('config', {})
    if config.get('neutral_fragments') or config.get('semantic_mapping'):
        raise ValueError('Use progressive main-method traces for migration replay')
    if config.get('progressive_fragments') != PROGRESSIVE_VERSION:
        raise ValueError('Unsupported progressive source version')
    if config.get('integrated_method') not in (None, METHOD_VERSION):
        raise ValueError('Unsupported main-method source version')
    if config.get('source_choices') not in (None, CLARITY_VERSION):
        raise ValueError('Unsupported source-choice version')
    optimized = deterministic_empty_relations or record.get('config', {}).get('deterministic_empty_relations', False)
    if optimized:
        from .deterministic_calls import wrap
        saved_call = call
        # For a baseline trace, consume and compare each removed model result.
        # For a new optimized trace, that call was never sent to the model.
        def check_skip(stage, prompt, payload, result):
            if not record.get('config', {}).get('deterministic_empty_relations', False):
                assert saved_call(stage, prompt, payload) == result, 'Skipped response differs'
        call = wrap(call, skipped, check_skip)
    episode = {}
    reference = record['simulator_sql_reference']['sql']
    simulator = frozen_module('simulator')
    def ask(question, history):
        return simulator.simulate_requester_with_sql(call, record['reference_request'],
            question, history, units, reference)
    with MainMethod(clarity_first=bool(record.get('config', {}).get('source_choices'))).installed():
        delivery = frozen_module('harness').run_session(call, record['request'],
            context, record['db_id'], record['allowed_columns'], ask,
            max_turns=record['config']['max_turns'], episode=episode, method='main')
    assert position == len(calls), ('Unused recorded calls', position, len(calls))
    assert episode['session']['current_request'] == record['episode']['session']['current_request']
    for key in ('action', 'reason', 'text', 'available_for_text2sql'):
        assert delivery.get(key) == record['delivery'].get(key), ('Delivery changed', key)
    def comparable(session):
        state = deepcopy(session)
        # v3.1.1 corrected this audit label, not the deletion transaction.
        for event in state.get('reduction_audit', []):
            if event.get('method') in ('focused_deletion_lineage_v3_1', VERSION):
                event['method'] = VERSION
        return state
    assert comparable(episode['session']) == comparable(record['episode']['session']), 'Final session changed'
    assert [comparable(s) for s in episode.get('snapshots', [])] == [
        comparable(s) for s in record['episode'].get('snapshots', [])], 'Intermediate sessions changed'
    assert episode.get('interactions', []) == record['episode'].get('interactions', []), 'Interactions changed'
    return {'case': f"{record['question_id']}_{record['level']}", 'calls_replayed': position,
        'delivery': delivery, 'session': episode['session'],
        'final_state_matches': True, 'intermediate_states_match': True,
        'interactions_match': True, 'deterministic_calls': skipped,
        'source_delivery_method': record['delivery'].get('method')}


def main(argv=None):
    import argparse
    from pathlib import Path
    from .runtime import runtime_hashes, engine_hashes
    from .storage import atomic_json
    from .inputs import load_source
    from .schema import schema_context
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()):
        parser.error('--out must be new or empty')
    args.out.mkdir(parents=True, exist_ok=True)
    schemas = json.loads((ROOT / 'artifacts/bird_mini_dev_quality_audit_20261009/schemas.json').read_text(encoding='utf8'))
    before = engine_hashes()
    rows = []
    for path in sorted(args.source.glob('*_*_reduction.json')):
        record = json.loads(path.read_text(encoding='utf8'))
        source = load_source(ROOT / 'data/selected/bird/flat_three_distinct_330_seed42', record['question_id'])
        result = replay(record, schema_context(schemas[record['db_id']]), source['flat_units'])
        atomic_json(args.out / path.name, result)
        rows.append({k: v for k, v in result.items() if k != 'session'})
        print(result['case'], result['calls_replayed'], result['delivery']['action'], flush=True)
    manifest = json.loads((args.source / 'CONFIG.json').read_text(encoding='utf8'))
    expected = {f'{i}_{l}' for i in manifest['ids'].split(',') for l in manifest['levels'].split(',')}
    assert {r['case'] for r in rows} == expected
    assert engine_hashes() == before
    atomic_json(args.out / 'REPLAY.json', {'source': str(args.source.resolve()),
        'runtime_source_sha256': runtime_hashes(), 'model_calls': 0,
        'engine_unchanged': True, 'all_saved_calls_matched': True, 'cases': rows})


if __name__ == '__main__':
    main()

