"""Local source ownership and reproducibility; no archived Python imports."""
import hashlib
from importlib import import_module
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent


def engine_module(name):
    return import_module('.engine.' + name, __package__)


def engine_hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((PACKAGE / 'engine').glob('*.py'))}


def runtime_hashes():
    files = [p for p in PACKAGE.rglob('*.py') if 'tests' not in p.parts]
    files.append(PACKAGE / 'matrix_cases.json')
    return {p.relative_to(PACKAGE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files)}


def snapshot_sources(out):
    hashes = runtime_hashes()
    for name in hashes:
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((PACKAGE / name).read_bytes())
    return hashes
