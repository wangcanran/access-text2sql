"""Resolve recorded response references identically for reuse and replay."""
import json
from pathlib import Path


def resolve_saved_call(entry, root, *, model=None):
    seen = set()
    while 'reused_from' in entry:
        origin = entry['reused_from']
        if (not isinstance(origin, dict) or not isinstance(origin.get('path'), str)
                or type(origin.get('index')) is not int or origin['index'] < 0):
            raise ValueError('Invalid saved response reference')
        path = Path(origin['path'])
        if not path.is_absolute():
            path = root / path
        marker = (str(path.resolve()), origin['index'])
        if marker in seen:
            raise ValueError('Cyclic saved response reference')
        seen.add(marker)
        record = json.loads(path.read_text(encoding='utf8'))
        if model is not None and record.get('model') != model:
            raise ValueError('Cached response model differs')
        source = record['calls'][origin['index']]
        if (entry['stage'] != source['stage']
                or entry.get('request_options', {}) != source.get('request_options', {})):
            raise ValueError('Cached response stage or generation options differ')
        for key in ('messages', 'input_signature'):
            if key in entry and key in source and entry[key] != source[key]:
                raise ValueError('Cached response input differs')
        entry = source
    return entry
