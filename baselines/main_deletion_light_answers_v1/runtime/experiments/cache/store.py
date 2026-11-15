"""Bounded, exact-input artifact cache. Never match by semantic similarity."""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from ...storage import atomic_json

VERSION = 'validated_artifacts_v1'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()


class ArtifactCache:
    def __init__(self, *, namespace, directory=None, max_entries=256):
        if max_entries < 1:
            raise ValueError('Cache capacity must be positive')
        self.namespace, self.capacity = namespace, max_entries
        self.directory = Path(directory) if directory is not None else None
        self.memory, self.audit = OrderedDict(), []

    def key(self, kind, inputs):
        return fingerprint([VERSION, self.namespace, kind, inputs])

    def get(self, kind, inputs):
        key = self.key(kind, inputs)
        value, source = self.memory.get(key), 'memory'
        if value is None and self.directory is not None:
            try:
                envelope = json.loads((self.directory / (key + '.json')).read_text(encoding='utf8'))
                if (envelope.get('key') == key and envelope.get('version') == VERSION
                        and envelope.get('sha256') == fingerprint(envelope.get('value'))):
                    value = {'value': envelope['value']}
                    source = 'disk'
            except (OSError, ValueError, TypeError, AttributeError):
                pass  # A damaged cache is a miss, never a broken query.
        hit = value is not None
        self.audit.append({'kind': kind, 'key': key, 'hit': hit, 'source': source if hit else None})
        if hit:
            self._remember(key, value)
            return True, deepcopy(value['value'])
        return False, None

    def _remember(self, key, value):
        self.memory[key] = deepcopy(value)
        self.memory.move_to_end(key)
        while len(self.memory) > self.capacity:
            self.memory.popitem(last=False)

    def put(self, kind, inputs, value, *, persist=False):
        key = self.key(kind, inputs)
        self._remember(key, {'value': value})
        if persist and self.directory is not None:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                atomic_json(self.directory / (key + '.json'), {'version': VERSION,
                    'key': key, 'sha256': fingerprint(value), 'value': value})
            except OSError:
                self.audit.append({'kind': kind, 'key': key, 'write_failed': True})

    def discard(self, kind, inputs):
        key = self.key(kind, inputs)
        self.memory.pop(key, None)
        if self.directory is not None:
            try:
                (self.directory / (key + '.json')).unlink(missing_ok=True)
            except OSError:
                pass

    def summary(self):
        result = {}
        for row in self.audit:
            if 'hit' in row:
                counts = result.setdefault(row['kind'], {'lookups': 0, 'hits': 0, 'misses': 0})
                counts['lookups'] += 1
                counts['hits' if row['hit'] else 'misses'] += 1
        return result
