"""Small free-decomposition pilot: no reference atoms are sent to the model."""
import json

from clarification_main.recorder import Recorder
from clarification_main.storage import atomic_json
from clarification_main.experiments.decision_update.pacing import RateGate
from .algebra import Requirements


PROMPT = '''Represent a database query request as independent contributions and
their scoped dependencies. Preserve the request's meaning and uncertainty.
Return only JSON:
{"atoms":{"a":"one contribution or prerequisite", "b":"another"},
 "roots":["a"], "requires":[["a","b"]]}.
roots are independently requested returns, population filters and result
instructions. Other atoms are prerequisites: subjects, associations, operands or
calculation-local conditions. A pair [a,b] means a needs b, not merely that they
are related. Share a prerequisite when multiple contributions need it.
Separate independently withdrawable returns, even inside a compound phrase.
Keep independent population filters separate from calculation-only filters.
Keep subjects and bindings explicit. Never turn a filter into an extra output.
Do not invent fields, definitions, SQL, tie policies or resolve vague expressions.
Keep AND/OR and negation in each condition; do not flatten Boolean expressions.
Use short natural-language atom texts in the original language. No commentary.
'''


def run(args, selected):
    recorder = Recorder(args.model, args.max_calls, args.reuse_dir,
                        empty_retries=1, transport_retries=0)
    recorder.client.chat.completions.create = RateGate(2).wrap(recorder.client.chat.completions.create)
    for c in selected:
        path = args.out / f"{c['name']}_free.json"
        record = {'question_id': c['name'], 'model': args.model,
                  'request': c['request'], 'calls': [], 'human_inventory_in_payload': False}
        call = recorder.bind(record, path, 0, 'free_inventory')
        try:
            raw = call('withdrawal_free_decomposition', PROMPT, {'request': c['request']})
            atoms = raw['atoms']
            if not isinstance(atoms, dict) or any(not isinstance(v, str) or not v.strip()
                                                   for v in atoms.values()):
                raise ValueError('atoms must map IDs to nonempty meanings')
            graph = Requirements.parse({k: raw[k] for k in ('roots', 'requires')}, atoms)
            record.update(atoms=atoms, graph=graph.json(),
                          initial_active=sorted(graph.live()), structural_valid=True)
        except Exception as exc:
            record.update(error=f'{type(exc).__name__}: {exc}', structural_valid=False)
        fresh = [x for x in record['calls'] if 'reused_from' not in x]
        record['cost'] = {'requests': len(fresh),
            'reported_tokens': sum((x.get('usage') or {}).get('total_tokens', 0) for x in fresh),
            'api_seconds': sum(x.get('elapsed_seconds', 0) for x in fresh)}
        atomic_json(path, record)
        print(json.dumps({'case': c['name'], 'valid': record['structural_valid'],
                          'cost': record['cost']}, ensure_ascii=False), flush=True)
