"""Run the existing benchmark client against a local OpenAI-compatible endpoint.

All ordinary predict_cloud.py arguments are forwarded unchanged. Only the
provider URL/key environment are configured here; prompts/cleaning stay upstream.
"""
import os
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'rbac-exp'))

from predict.cloud_client import PROVIDERS, APIConfig, CloudAPIClient

PROVIDERS['openai'] = APIConfig(
    url=os.environ.get('RBAC_API_BASE', 'http://127.0.0.1:8000/v1').rstrip('/') + '/chat/completions',
    key_env='RBAC_API_KEY', default_model='text2sql', default_workers=4)
os.environ.setdefault('RBAC_API_KEY', 'local')

class AuditedClient(CloudAPIClient):
    """Keep original requests/prompts but expose transport and truncation failures."""
    _audit_lock = threading.Lock()
    _local = threading.local()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timeout = int(os.environ.get('RBAC_REQUEST_TIMEOUT', str(self.timeout)))

    def _build_payload(self, prompt, max_tokens=2048, temperature=0.0):
        payload = super()._build_payload(prompt, max_tokens, temperature)
        if self.model_name in ('deepseek-v4-pro', 'deepseek-flash') and os.environ.get('RBAC_DEEPSEEK_THINKING') == 'disabled':
            payload['thinking'] = {'type': 'disabled'}
        return payload

    def call(self, prompt, **kwargs):
        self._local.prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        result = super().call(prompt, **kwargs)
        if not result:
            raise RuntimeError('Empty API response after retries')
        return result

    def _extract_response(self, data):
        path = os.environ.get('RBAC_RESPONSE_AUDIT')
        if path:
            record = {'time': time.time(), 'prompt_sha256': self._local.prompt_hash,
                      'response': data}
            with self._audit_lock, open(path, 'a', encoding='utf8') as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + '\n')
        if data.get('choices', [{}])[0].get('finish_reason') == 'length':
            raise RuntimeError('Generation truncated at max_tokens; see response audit')
        message = data['choices'][0]['message']
        content = message.get('content')
        if content is not None and not content.strip():
            return 'Error: Model emitted no final answer'
        return super()._extract_response(data)


def clean_final_response(response):
    """Exclude reasoning and never reward an unparseable answer as a refusal."""
    from data_process.response_cleaner import clean_model_response
    if response.startswith('Error:'):
        return response
    text = response
    if '</think>' in text:
        text = text.rsplit('</think>', 1)[1].strip()
    elif '<think>' in text:
        return 'Error: Model emitted unfinished reasoning'
    if not text.strip():
        return 'Error: Model emitted no final answer'
    cleaned = clean_model_response(text)
    if cleaned.lower() == 'empty response':
        return 'Error: Model emitted no parseable answer'
    return cleaned

if __name__ == '__main__':
    from predict import predict_cloud
    predict_cloud.CloudAPIClient = AuditedClient
    predict_cloud.clean_model_response = clean_final_response
    predict_cloud.main()
