"""Serial fresh-call runner for the independent clarification tree experiment."""
import argparse
import os
from pathlib import Path

from ...live import load_case
from ...engine.simulator import simulate_requester_with_sql
from ...recorder import Recorder, cost
from ...runtime import snapshot_sources, runtime_hashes
from ...storage import atomic_json
from .focused_runner import run, VERSION


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases', default='113:1')
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--model', default='gpt-5.5')
    p.add_argument('--turns', type=int, default=40)
    p.add_argument('--max-calls', type=int, default=80)
    p.add_argument('--answer-package', action='store_true', help='Experiment: compile answer and local refinement in one model response')
    p.add_argument('--compact-json', action='store_true', help='Experiment: remove JSON separator whitespace only')
    args = p.parse_args(argv)
    try:
        cases = [tuple(map(int, s.split(':'))) for s in args.cases.split(',')]
        if any(len(c) != 2 or c[1] not in (0, 1, 2) for c in cases) or len(set(cases)) != len(cases):
            raise ValueError
    except ValueError:
        p.error('--cases requires distinct question_id:level pairs')
    if args.turns < 0 or args.max_calls < 1:
        p.error('Invalid budget')
    if args.out.exists() and (not args.out.is_dir() or any(args.out.iterdir())):
        p.error('--out must be new or empty')
    # Validate fixtures before creating artifacts or clients.
    loaded = [(qid, level, load_case(qid, level)) for qid, level in cases]
    args.out.mkdir(parents=True, exist_ok=True)
    hashes = snapshot_sources(args.out / 'sources')
    atomic_json(args.out / 'CONFIG.json', {**vars(args), 'out': str(args.out.resolve()),
        'version': VERSION, 'runtime_source_sha256': hashes, 'gold_sql_simulator_only': True,
        'serial': True, 'fresh_calls': True, 'legacy_session_used': False})
    summary = []
    for qid, level, (source, context, role, allowed, reference) in loaded:
        path = args.out / f'{qid}_{level}_reduction.json'
        record = {'question_id': qid, 'level': level, 'model': args.model,
            'request': source['levels'][level]['rewritten_request'], 'db_id': source['db_id'],
            'role': role['role'], 'allowed_columns': allowed, 'source_sha256': source['source_sha256'],
            'config': {'version': VERSION, 'sql_execution_evaluated': False},
            'calls': [], 'episode': {}, 'runtime_source_sha256': hashes}

        def save():
            atomic_json(path, record)

        save()
        try:
            if runtime_hashes() != hashes:
                raise RuntimeError('Sources changed after snapshot')
            recorder = Recorder(args.model, args.max_calls, [], timeout=120,
                                empty_retries=1, transport_retries=1, compact_json=args.compact_json)
            call = recorder.bind(record, path, 0, VERSION)

            def ask(question, history):
                return simulate_requester_with_sql(call, source['question'], question,
                    history, source.get('flat_units', []), reference['sql'])

            record['delivery'] = run(call, record['request'], context, allowed, ask,
                                    max_turns=args.turns, episode=record['episode'], save=save, semantic_review=True,
                                    answer_package=args.answer_package)
            record['outcome'] = record['delivery']['action']
        except Exception as exc:
            record.update(outcome='system_error', error_type=type(exc).__name__,
                          error=str(exc).replace(os.getenv('OPENAI_API_KEY') or '<no-key>', '<redacted>'))
        record['runtime_unchanged'] = runtime_hashes() == hashes
        if not record['runtime_unchanged']:
            record.update(outcome='system_error', error_type='RuntimeSourceChanged')
            record['delivery'] = {'action': 'system_error', 'available_for_text2sql': False, 'text': ''}
        record['cost'] = cost(record['calls'])
        save()
        row = {'case': [qid, level], 'outcome': record['outcome'], 'cost': record['cost']}
        summary.append(row)
        atomic_json(args.out / 'SUMMARY.json', summary)
        print(row, flush=True)


if __name__ == '__main__':
    main()

