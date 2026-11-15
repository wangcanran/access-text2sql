"""Bounded model transport and exact-input cache, shared by live experiments."""
from copy import deepcopy
import hashlib
import json
import os
import time
from .storage import atomic_json
from .runtime import ROOT
from .engine import simple_session as engine
from .response_boundary import decode_response, ResponseBoundaryError, transient_transport_kind
from .saved_calls import resolve_saved_call

def cost(calls):
    return {'logical_calls': len(calls), **{k: sum(c.get('usage', {}).get(k, 0) for c in calls)
            for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}}



class Recorder:
    def __init__(self, model, max_calls, reuse_dirs, offline=False, timeout=120, empty_retries=1,
                 stage_reasoning=None, transport_retries=0, compact_json=False):
        self.compact_json = compact_json
        if type(empty_retries) is not int or not 0 <= empty_retries <= 2:
            raise ValueError('empty_retries must be between zero and two')
        self.empty_retries = empty_retries
        if type(transport_retries) is not int or not 0 <= transport_retries <= 2:
            raise ValueError('transport_retries must be between zero and two')
        self.transport_retries = transport_retries
        self.stage_reasoning = dict(stage_reasoning or {})
        if any(not isinstance(stage, str) or not stage or stage.endswith('_contract_retry')
               or effort not in ('low', 'medium', 'high')
               for stage, effort in self.stage_reasoning.items()):
            raise ValueError('Use base stage names and low/medium/high reasoning effort')
        self.model, self.max_calls, self.attempted = model, max_calls, 0
        self.cache = {}
        for folder in reuse_dirs:
            for p in folder.glob('*.json'):
                raw = json.loads(p.read_text(encoding='utf8'))
                if not isinstance(raw, dict) or raw.get('model') != model:
                    continue
                for i, e in enumerate(raw.get('calls', [])):
                    e = resolve_saved_call(e, ROOT, model=model)
                    if e.get('serialization', 'default') != ('compact_json_v1' if self.compact_json else 'default'):
                        continue
                    if 'response' in e and 'messages' in e:
                        try:
                            parsed = decode_response(e['response'])
                        except ResponseBoundaryError:
                            continue
                        ms = e['messages']
                        key = self.input_key(e['stage'], ms[0]['content'],
                            json.loads(ms[1]['content']), e.get('request_options', {}))
                        self.cache[key] = (parsed,
                            e['response'].get('usage') or {}, {'path': str(p.resolve()), 'index': i,
                            'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})
        if not offline:
            from openai import OpenAI
            from .environment import load_dotenv
            # The migrated runner owns its configuration; do not depend on the
            # historical experiment directory for credentials.
            load_dotenv()
            self.client = OpenAI(api_key=os.environ['OPENAI_API_KEY'], base_url=os.getenv('OPENAI_BASE_URL'),
                                 timeout=timeout, max_retries=0)

    def request_options(self, stage):
        effort = self.stage_reasoning.get(stage.removesuffix('_contract_retry'))
        return {'reasoning_effort': effort} if effort else {}

    def input_key(self, stage, prompt, payload, options):
        # Preserve historical keys when the API defaults are unchanged. A new
        # explicit effort must never reuse a response generated at another effort.
        parts = (self.model, stage, prompt, payload)
        if self.compact_json:
            return engine.signature(*parts, options, {'serialization': 'compact_json_v1'})
        return engine.signature(*parts, options) if options else engine.signature(*parts)

    def bind(self, record, path, round_index, arm):
        def call(stage, prompt, payload):
            options = self.request_options(stage)
            key = self.input_key(stage, prompt, payload, options)
            if key in self.cache:
                raw, usage, origin = self.cache[key]
                record['calls'].append({'stage': stage, 'round': round_index, 'arm': arm,
                    'reused_from': origin, 'usage': usage, 'request_options': options})
                atomic_json(path, record)
                return deepcopy(raw)
            messages = [{'role': 'system', 'content': prompt},
                        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False,
                            **({'separators': (',', ':')} if self.compact_json else {}))}]
            empty_used, transport_used = 0, 0
            for attempt in range(self.empty_retries + self.transport_retries + 1):
                if self.attempted >= self.max_calls:
                    raise RuntimeError('API attempt budget reached')
                self.attempted += 1
                entry = {'stage': stage, 'round': round_index, 'arm': arm,
                         'messages': deepcopy(messages), 'response_attempt': attempt + 1,
                         'input_signature': key, 'request_options': options}
                entry['serialization'] = 'compact_json_v1' if self.compact_json else 'default'
                entry['retry_limits'] = {'response': self.empty_retries, 'transport': self.transport_retries}
                record['calls'].append(entry)
                atomic_json(path, record)
                print(record['question_id'], round_index, arm, stage, self.attempted, flush=True)
                start = time.perf_counter()
                try:
                    response = self.client.chat.completions.create(model=self.model, temperature=0,
                        response_format={'type': 'json_object'}, messages=entry['messages'], **options)
                    entry.update(response=response.model_dump(mode='json'),
                                 usage=response.usage.model_dump() if response.usage else {})
                    entry['request_id'] = getattr(response, '_request_id', None)
                    parsed = decode_response(entry['response'])
                    entry['response_status'] = 'valid_json_object'
                except ResponseBoundaryError as exc:
                    retry = exc.retryable and empty_used < self.empty_retries and self.attempted < self.max_calls
                    entry.update(response_status=exc.kind, failure_layer='response_boundary',
                                 retry_scheduled=retry, error=str(exc))
                    if not retry:
                        raise
                    empty_used += 1
                except Exception as exc:
                    kind = transient_transport_kind(exc)
                    retry = bool(kind and transport_used < self.transport_retries and self.attempted < self.max_calls)
                    entry.update(error_type=type(exc).__name__, failure_layer='transport_or_sdk',
                        response_status='transport_failure', transport_kind=kind, retry_scheduled=retry,
                        error=str(exc).replace(os.environ.get('OPENAI_API_KEY', '<none>'), '<redacted>'))
                    if not retry:
                        raise
                    transport_used += 1
                finally:
                    entry['elapsed_seconds'] = time.perf_counter() - start
                    atomic_json(path, record)
                if entry['response_status'] != 'valid_json_object':
                    # Same prompt, no invented assistant answer; each attempt is
                    # charged to the API budget and retained as distinct evidence.
                    delay = min(2 ** attempt, 8)
                    entry['retry_delay_seconds'] = delay
                    atomic_json(path, record)
                    time.sleep(delay)
                    continue
                self.cache[key] = (deepcopy(parsed), entry['usage'],
                    {'path': str(path), 'index': len(record['calls']) - 1})
                return parsed
        return call

