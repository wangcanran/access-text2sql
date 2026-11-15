"""Atomic checkpoints and paid-response reuse for interrupted generation."""
import hashlib
import json
import os
import time
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf8', newline='\n') as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    # A Windows reader (including a file indexer) can briefly hold the target
    # without delete-sharing. Retry only that transient access/sharing error;
    # keep the fully flushed temp file and never rerun the paid model call.
    for attempt in range(6):
        try:
            os.replace(temp, path)
            break
        except PermissionError as exc:
            if getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == 5:
                raise
            time.sleep(0.05 * (2 ** attempt))


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


class ResponseCache:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.hits = 0
        self.calls = 0

    def __call__(self, stage, request, invoke):
        key = {'stage': stage, 'request': request}
        path = self.directory / (digest(key) + '.json')
        if path.exists():
            row = json.loads(path.read_text(encoding='utf8'))
            if row['key'] != key:
                raise ValueError('Response cache identity mismatch')
            self.hits += 1
            return row['response']
        response = invoke()
        # Invalid JSON cannot advance the pipeline; keep it for audit, but retry
        # this request on resume instead of permanently replaying a parse error.
        try:
            json.loads(response['choices'][0]['message']['content'])
        except (ValueError, KeyError, TypeError, IndexError):
            atomic_json(self.directory / (digest(key) + '.invalid.json'),
                        {'key': key, 'response': response})
            raise ValueError('API response did not contain valid JSON')
        atomic_json(path, {'key': key, 'response': response})
        self.calls += 1
        return response


class RunLock:
    """OS releases the lock on exit or crash; no stale-PID recovery needed."""
    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        self.handle = self.path.open('a+b')
        try:
            if os.fstat(self.handle.fileno()).st_size == 0:
                self.handle.write(b'0')
                self.handle.flush()
            self.handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise ValueError('Another process is using this output directory')
        return self

    def __exit__(self, *args):
        self.handle.close()
