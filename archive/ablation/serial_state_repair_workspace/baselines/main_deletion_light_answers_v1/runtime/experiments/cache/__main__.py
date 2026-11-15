"""Serial artifact-cache pilot on main deletion + lightweight answers."""
import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch

from ... import live
from ...environment import load_dotenv
from ...runtime import snapshot_sources, runtime_hashes
from ...storage import atomic_json
from ..decision_update.pacing import RateGate
from .baseline import LightMainMethod, VERSION
from .method import CachedLightMain
from .store import ArtifactCache, fingerprint


def cache_namespace(model, endpoint, options, sources):
    # Endpoint/model/settings/code changes cannot share semantic artifacts.
    return fingerprint({'baseline': VERSION, 'model': model,
        'endpoint': (endpoint or 'https://api.openai.com/v1').rstrip('/'),
        'request_options': options, 'runtime_source_sha256': sources})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', default='796:0,24:0,113:1')
    parser.add_argument('--variants', default='off,on')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--model', default='gpt-5.5')
    parser.add_argument('--turns', type=int, default=30)
    parser.add_argument('--max-calls', type=int, default=120)
    parser.add_argument('--min-call-interval', type=float, default=2.0)
    args = parser.parse_args()
    cases = [tuple(map(int, part.split(':'))) for part in args.cases.split(',')]
    variants = args.variants.split(',')
    if (any(len(c) != 2 or c[1] not in (0, 1, 2) for c in cases)
            or len(set(cases)) != len(cases) or len(set(variants)) != len(variants)
            or any(v not in ('off', 'on') for v in variants)):
        parser.error('Use distinct question_id:level cases and off,on variants')
    if args.turns < 0 or args.max_calls < 1:
        parser.error('Invalid turn/call budget')
    if args.out.exists() and any(args.out.iterdir()):
        parser.error('Output directory must be new or empty')
    args.out.mkdir(parents=True, exist_ok=True)
    load_dotenv()
    sources = snapshot_sources(args.out / 'sources')
    namespace = cache_namespace(args.model, os.getenv('OPENAI_BASE_URL'), {}, sources)
    cache = ArtifactCache(namespace=namespace, directory=args.cache_dir)
    atomic_json(args.out / 'CONFIG.json', {**{k: str(v) if isinstance(v, Path) else v
        for k, v in vars(args).items()}, 'runtime_source_sha256': sources,
        'baseline': VERSION, 'cache_namespace': namespace, 'gold_sql_simulator_only': True})
    recorder_class = live.Recorder
    gate = RateGate(args.min_call_interval)
    def recorder_factory(*a, **kw):
        recorder = recorder_class(*a, **kw)
        recorder.client.chat.completions.create = gate.wrap(recorder.client.chat.completions.create)
        return recorder
    for variant in variants:
        folder = args.out / variant
        folder.mkdir()
        atomic_json(folder / 'CONFIG.json', {'runtime_source_sha256': sources})
        for qid, level in cases:
            offset = len(cache.audit)
            def factory(**unused):
                return (CachedLightMain(namespace=namespace, cache=cache)
                        if variant == 'on' else LightMainMethod())
            run_args = argparse.Namespace(out=folder, model=args.model, turns=args.turns,
                max_calls=args.max_calls, reuse_dir=[], response_retries=1, transport_retries=1,
                clarity_first=True, disable_deterministic_empty_relations=False)
            with patch.object(live, 'MainMethod', factory), patch.object(live, 'Recorder', recorder_factory):
                outcome = live.run_case(run_args, qid, level)
            path = folder / f'{qid}_{level}_reduction.json'
            record = json.loads(path.read_text(encoding='utf8'))
            record['config'].update(method=VERSION, integrated_method=VERSION,
                reconstruction='light_answers_final_render_main_deletion_reconstruct',
                structure_update='light_answer_transaction_main_deletion_refinement',
                deletion='main_method_unchanged')
            record['experiment'] = {'variant': variant, 'baseline': VERSION,
                'saved_trace_replay': False, 'cache_namespace': namespace}
            record['cache_audit'] = cache.audit[offset:]
            record['runtime_unchanged'] = runtime_hashes() == sources
            atomic_json(path, record)
            atomic_json(args.out / 'CACHE_SUMMARY.json', cache.summary())
            print(variant, qid, level, outcome, record['actual_cost'], flush=True)


if __name__ == '__main__':
    main()
