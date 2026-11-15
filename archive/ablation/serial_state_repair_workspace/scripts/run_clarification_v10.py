"""Pinned v10 research entry point; never falls back to the legacy mechanism."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

from clarification_useful_residual_v6 import live
from clarification_useful_residual_v6.pool_method import VERSION


EXPECTED_VERSION = 'useful_residual_v6_pool_v10_2_prompt_chain'
ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'clarification_useful_residual_v6/V10_SOURCE_LOCK.json'
SOURCE_DIRS = {'method': 'clarification_useful_residual_v6',
               'overlay_v2': 'clarification_deferred_deletion',
               'overlay_v4': 'clarification_deferred_v3_2',
               'overlay_v5': 'clarification_contract_v5'}


def verify_sources():
    lock = json.loads(LOCK.read_text(encoding='utf8'))
    if lock.get('method_version') != EXPECTED_VERSION:
        raise ValueError('v10 source lock has a different method version')
    for label, folder in SOURCE_DIRS.items():
        expected = lock['source_sha256'][label]
        actual = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (ROOT / folder).glob('*.py')}
        if actual != expected:
            changed = sorted({name for name in expected.keys() | actual.keys()
                              if expected.get(name) != actual.get(name)})
            raise ValueError(f'v10 source lock mismatch in {label}: {changed}')


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cases')
    parser.add_argument('--max-cases', type=int)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--unresolved-retries', type=int, default=0,
                        help='Retry unresolved/system_error sessions N additional times')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--fixture-dir', type=Path,
                        help='Compiled three-dataset fixture directory')
    args = parser.parse_args()
    if VERSION != EXPECTED_VERSION:
        parser.error(f'Pool version changed: expected {EXPECTED_VERSION}, got {VERSION}')
    try:
        verify_sources()
    except (OSError, KeyError, ValueError) as exc:
        parser.error(str(exc))
    if not args.selection.is_file():
        parser.error(f'Selection does not exist: {args.selection}')
    if args.resume and args.check_only:
        parser.error('--resume and --check-only cannot be combined')
    if args.model in ('deepseek-v4-pro', 'deepseek-flash') and not args.check_only:
        from scripts.run_clarification_deepseek import configure, install_v4_non_thinking
        from clarification_deferred_deletion.baseline import module
        configure()
        install_v4_non_thinking(module('live').Recorder)
    forwarded = ['live', '--mechanism', 'obligation-pool', '--model', args.model,
                 '--selection', str(args.selection), '--out', str(args.out)]
    if args.cases:
        forwarded += ['--cases', args.cases]
    if args.max_cases is not None:
        forwarded += ['--max-cases', str(args.max_cases)]
    if args.resume:
        forwarded.append('--resume')
    if args.unresolved_retries:
        forwarded += ['--unresolved-retries', str(args.unresolved_retries)]
    if args.check_only:
        forwarded.append('--check-only')
    original_argv = sys.argv
    if args.fixture_dir:
        from clarification_deferred_deletion.baseline import module
        fixture = args.fixture_dir.resolve()
        for name in ('sources.json', 'roles.json', 'schemas.json', 'selection.json', 'MANIFEST.json'):
            if not (fixture / name).is_file():
                parser.error(f'Missing compiled fixture: {fixture / name}')
        if args.selection.resolve() != (fixture / 'selection.json').resolve():
            parser.error('--selection must be fixture-dir/selection.json')
        selection = json.loads(args.selection.read_text(encoding='utf8'))
        expected_hashes = selection.get('fixture_payload_sha256')
        if not isinstance(expected_hashes, dict):
            parser.error('Compiled fixture lacks source/role/schema hashes')
        for name, expected in expected_hashes.items():
            path = fixture / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                parser.error(f'Compiled fixture changed: {path}')
        sources = json.loads((fixture / 'sources.json').read_text(encoding='utf8'))
        frozen = module('live')

        def fixture_source(path, qid):
            row = sources[str(qid)]
            if row['question_id'] != qid:
                raise ValueError('Fixture source ID differs')
            # JSON object keys are strings; the frozen runtime indexes levels
            # with integer IDs. Normalize only at the fixture adapter boundary.
            return {**row, 'levels': {int(level): value for level, value in row['levels'].items()}}

        def fixture_reference(path, source):
            return {'sql': source['reference_sql'], 'question_id': source['question_id'],
                    'path': str(fixture / 'sources.json')}

        with patch.object(frozen, 'DATA', fixture / 'sources.json'), \
             patch.object(frozen, 'POLICIES', fixture / 'roles.json'), \
             patch.object(frozen, 'SCHEMAS', fixture / 'schemas.json'), \
             patch.object(frozen, 'GOLD', fixture / 'sources.json'), \
             patch.object(frozen, 'load_source', fixture_source), \
             patch.object(frozen, 'load_reference_sql', fixture_reference):
            try:
                sys.argv = forwarded
                live.main()
            finally:
                sys.argv = original_argv
    else:
        try:
            sys.argv = forwarded
            live.main()
        finally:
            sys.argv = original_argv


if __name__ == '__main__':
    main()
