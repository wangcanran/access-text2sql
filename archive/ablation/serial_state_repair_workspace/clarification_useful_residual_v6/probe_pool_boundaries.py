"""Fresh model responses on frozen real failure inputs; NOT fresh episodes."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from clarification_deferred_deletion.baseline import ROOT, analysis, light, module
from .live import source_hashes
from .pool_method import PoolMethod, VERSION
from .pool_answer_review import PROMPT, validate


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--model', default='gpt-5.2')
    args = parser.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        parser.error('Use a new or empty output directory')
    args.out.mkdir(parents=True, exist_ok=True)
    module('environment').load_dotenv(ROOT/'clarification_main/.env')
    live = module('live')
    storage = module('storage')
    sources = source_hashes(Path(__file__).parent)
    storage.atomic_json(args.out/'CONFIG.json', dict(method=VERSION, model=args.model,
        kind='fresh_model_on_frozen_failure_inputs', fresh_episode=False,
        method_source_sha256=sources))
    for path in Path(__file__).parent.glob('*.py'):
        target = args.out/'method_sources'/path.name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(path.read_bytes())
    schemas = json.loads(live.SCHEMAS.read_text(encoding='utf8'))
    results = []
    fixtures = [
        ('1484_1', 'gpt52_pool_v5_contract4_20261028', 'missing_dependency'),
        ('223_1', 'gpt52_pool_v6_dependencies2_20261028', 'public_wording'),
    ]
    for key, folder, kind in fixtures:
        original = ROOT/'artifacts/clarification_main'/folder/(key+'_reduction.json')
        data = json.loads(original.read_text(encoding='utf8'))
        context = live.schema_context(schemas[data['db_id']])
        record = dict(question_id=key, model=args.model, calls=[], source_trace=str(original),
                      kind=kind, fresh_episode=False)
        path = args.out/(key+'_probe.json')
        recorder = module('recorder').Recorder(args.model, 4, [], empty_retries=1, transport_retries=1)
        call = recorder.bind(record, path, 0, 'boundary_probe')
        if kind == 'missing_dependency':
            session = data['episode']['session']
            answer = session['turns'][-1]
            owners = session['view']['items']
            payload = dict(answer_text=answer['answer']['text'],
                asked_question=answer['question'], schema=light.schema_payload(context),
                replacements=[dict(id=i, requirement=n['requirement'],
                    binding_claims=session['binding_claims'][i]) for i, n in owners.items()])
            result = analysis.validated_call(call, 'answer_contribution_review', PROMPT, payload,
                lambda raw: validate(raw, payload['answer_text']))
            passed = not result['accepted'] and any(i['code'] == 'missing_dependency' for i in result['issues'])
        else:
            source = next(c for c in data['calls'] if c['stage'] == 'public_wording')
            public = json.loads(source['messages'][1]['content'])['public']
            result = PoolMethod()._public_text(call, deepcopy(public), context)
            passed = True  # _public_text validated exact structure and leak absence.
        record.update(result=result, passed=passed, cost=module('recorder').cost(record['calls']))
        storage.atomic_json(path, record)
        results.append(dict(probe=key, kind=kind, passed=passed, cost=record['cost']))
        print(key, 'PASSED' if passed else 'FAILED', flush=True)
    storage.atomic_json(args.out/'PROBES.json', dict(fresh_episode=False, results=results,
        source_unchanged=sources == source_hashes(Path(__file__).parent)))
    if not all(r['passed'] for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
