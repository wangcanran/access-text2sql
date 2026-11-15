"""Load and verify the exact baseline used for the preceding three live cases."""
import importlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / 'baselines/main_deletion_light_answers_v1'
NAME = '_batch_frozen_light_main'


def module(suffix):
    return importlib.import_module(NAME + '.' + suffix)


if NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(NAME, FROZEN / 'runtime/__init__.py',
        submodule_search_locations=[str(FROZEN / 'runtime')])
    package = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = package
    spec.loader.exec_module(package)
    manifest = json.loads((FROZEN / 'MANIFEST.json').read_text(encoding='utf8'))
    runtime = module('runtime')
    if runtime.runtime_hashes() != manifest['runtime_source_sha256']:
        raise RuntimeError('Immutable combination baseline hash mismatch')
    runtime.ROOT = ROOT

LightMainMethod = module('experiments.cache.baseline').LightMainMethod
light = module('experiments.decision_update.lightweight')
analysis = module('engine.analysis')
updates = module('engine.session_update')
main = module('method')
