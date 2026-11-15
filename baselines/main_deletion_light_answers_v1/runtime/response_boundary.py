"""Classify API responses before semantic contract validation."""
import json


def transient_transport_kind(exc):
    """Only retry recognizable transient SDK/network failures, never contracts."""
    from openai import APIConnectionError, APIStatusError
    if isinstance(exc, (TimeoutError, APIConnectionError)):
        return 'connection_or_timeout'
    if isinstance(exc, APIStatusError) and exc.status_code in (408, 429, 500, 502, 503, 504):
        return 'http_' + str(exc.status_code)
    return None


class ResponseBoundaryError(RuntimeError):
    def __init__(self, kind, *, retryable=False):
        self.kind, self.retryable = kind, retryable
        super().__init__('Model response boundary failure: ' + kind)


def decode_response(raw):
    choices = raw.get('choices')
    if not isinstance(choices, list) or not choices:
        raise ResponseBoundaryError('missing_choices', retryable=True)
    choice = choices[0]
    message = choice.get('message') or {}
    reason = choice.get('finish_reason')
    if message.get('refusal') or reason == 'content_filter':
        raise ResponseBoundaryError('model_refusal')
    if message.get('tool_calls') or message.get('function_call'):
        raise ResponseBoundaryError('unexpected_tool_call')
    if reason == 'length':
        raise ResponseBoundaryError('truncated_output')
    content = message.get('content')
    if content is None or isinstance(content, str) and not content.strip():
        raise ResponseBoundaryError('empty_content', retryable=True)
    if not isinstance(content, str):
        raise ResponseBoundaryError('invalid_content_type')
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ResponseBoundaryError('invalid_json') from exc
    if not isinstance(parsed, dict):
        raise ResponseBoundaryError('non_object_json')
    return parsed
