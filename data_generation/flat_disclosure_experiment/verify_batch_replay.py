"""Exercise the real generator with saved API responses, including interruption.

No network calls. Supply a previous three/four-level smoke trace whose requests
cover the new 0/0.5/1 plan (the audited school q7 trace has two counted fragments).
"""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from .batch_generate import DEFAULT_SCHEMAS, execute
from .storage import atomic_json, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    old = json.loads(args.trace.read_text(encoding='utf8'))
    recorded = {digest(c['request']): c['response'] for c in old['calls'] if 'response' in c}
    case = {k: old[k] for k in ('question_id', 'db_id', 'question')}
    schemas = json.loads(DEFAULT_SCHEMAS.read_text(encoding='utf8'))
    options = SimpleNamespace(output=args.output, model='gpt-5.1', review_model='gpt-5.5',
                              workers=1, seed=42, repair_attempts=1, resume=False, max_cases=None)
    calls = []
    interrupt = True

    def create(**request):
        nonlocal interrupt
        if interrupt and len(calls) == 5:
            interrupt = False
            raise KeyboardInterrupt('simulated hard interruption after five persisted API responses')
        key = digest(request)
        if key not in recorded:
            raise AssertionError('New request is not covered by saved live fixture')
        calls.append(key)
        return SimpleNamespace(model_dump=lambda **kw: recorded[key])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with patch.dict(os.environ, {'OPENAI_API_KEY': 'offline-fixture', 'OPENAI_BASE_URL': 'https://offline.invalid/v1'}), patch(
            'flat_disclosure_experiment.live_smoke.OpenAI', return_value=client):
        try:
            execute(options, [case], schemas)
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError('Interruption was not exercised')
        options.resume = True
        summary = execute(options, [case], schemas)
        assert summary['completed'] == 1 and summary['levels'] == 3, summary
        assert len(set(calls)) == len(calls), 'A previously completed request was invoked twice'
        before = len(calls)
        execute(options, [case], schemas)
        assert len(calls) == before, 'Completed case was invoked again'
    result = {'mode': 'offline replay of real saved API responses', 'network_calls': 0,
              'interrupted_after': 5, 'fixture_responses_used': len(calls),
              'duplicate_requests': 0, 'completed_resume_new_requests': 0, 'summary': summary}
    atomic_json(args.output / 'verification.json', result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
