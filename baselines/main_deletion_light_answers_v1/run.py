"""Verified main-deletion/light-answer combination, with cache disabled."""
import argparse
import importlib
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--check-only', action='store_true')
    args, forwarded = parser.parse_known_args()
    if '--variants' in forwarded or '--cache-dir' in forwarded:
        parser.error('This launcher forces variants=off and no persistent cache')
    sys.path.insert(0, str(ROOT))
    manifest = json.loads((HERE / 'MANIFEST.json').read_text(encoding='utf8'))
    name = '_frozen_main_deletion_light_answers'
    spec = importlib.util.spec_from_file_location(name, HERE / 'runtime/__init__.py',
        submodule_search_locations=[str(HERE / 'runtime')])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    runtime = importlib.import_module(name + '.runtime')
    if runtime.runtime_hashes() != manifest['runtime_source_sha256']:
        raise RuntimeError('Combination source hash mismatch')
    runtime.ROOT = ROOT
    importlib.import_module(name + '.environment').load_dotenv(ROOT / 'clarification_main/.env')
    baseline = importlib.import_module(name + '.experiments.cache.baseline')
    main_method = importlib.import_module(name + '.method').MainMethod
    assert baseline.LightMainMethod._remove is main_method._remove
    print('main_deletion_light_answers_v1: 136 source hashes verified; main deletion; cache OFF.', flush=True)
    if not args.check_only:
        sys.argv = [sys.argv[0], '--variants', 'off', *forwarded]
        importlib.import_module(name + '.experiments.cache.__main__').main()


if __name__ == '__main__':
    main()
