"""BIRD c-Interact-inspired clarification frontend; SQL authorization is deferred."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_strengthened_baselines as shared

VERSION = 'bird_conv_nl_v2_upstream_prompt_shared_user'
UPSTREAM_COMMIT = '451fe2c3518ee1cf908d8139e2913483bd519381'
UPSTREAM_PROMPT = ROOT / 'BIRD-Interact/bird_interact_conv/prompts/prompts.py'
# Load upstream templates and history builder, rather than rewriting their policy.
import importlib.util
import time
from copy import deepcopy

def load_upstream(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'BIRD-Interact' / relative)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

UP = load_upstream('_bird_conv_templates_v2', 'bird_interact_conv/prompts/prompts.py')
BUILDER = load_upstream('_bird_conv_builder_v2', 'bird_interact_conv/code/infer_api_system.py')

# Exhaustive textual substitutions; all other upstream wording stays unchanged.
SUBSTITUTIONS = [
    ('great SQL writing ability', 'great query clarification ability'),
    ('generating PostgreSQL', 'writing a complete clarified natural-language request'),
    ('Generate Final SQL', 'Submit Final Clarified Request'),
    ('final PostgreSQL', 'final clarified natural-language request'),
    ('final PostgreSQ', 'final clarified natural-language request'),
    ('updated PostgreSQL', 'updated clarified natural-language request'),
    ('```postgresql', '```text'),
    ('[FILL-YOUR-SQL]', '[FILL-YOUR-CLARIFIED-REQUEST]'),
    ('[YOUR-SQL]', '[YOUR-CLARIFIED-REQUEST]'),
]

def adapt(text):
    for old, new in SUBSTITUTIONS:
        text = text.replace(old, new)
    return text

PROMPT = adapt(UP.system_react)


def dialogue(payload, source, call, simulator, max_turns):
    # Same benchmark environment as the main method; no private label counts.
    # Upstream counts the final submission round; our --turns counts answers.
    data = dict(instance_id='case', user_query_ambiguity={'critical_ambiguity': []},
                knowledge_ambiguity=[])
    user = {}
    history = []
    initial = PROMPT
    for key, value in dict(user_query=payload['request'], DB_name=payload['database'],
                           DB_schema=json.dumps(payload['schema'], ensure_ascii=False),
                           external_kg='', max_turn=str(max_turns + 1)).items():
        initial = initial.replace('[[' + key + ']]', value)
    for turn in range(1, max_turns + 2):
        if turn == 1:
            prompt = initial
            # With zero answers allowed, enter the upstream final-round form.
            if max_turns == 0:
                prompt += '\n# It is the final turn. You MUST provide the final clarified natural-language request in <t>```text ... ```</t>.\n- You: <t>'
        else:
            data = BUILDER.wrap_up_prompt(data, '', '', UP.system_react,
                                          max_turns + 1, turn, user)
            prompt = adapt(data['prompt'])
        data['prompt_turn_' + str(turn)] = prompt
        response = call('baseline_bird_conv_nl', prompt, None)
        data['prediction_turn_' + str(turn)] = response
        text, terminal = BUILDER.extract_system_response(response)
        if terminal:
            text = text.strip()
            if text.startswith('```text') and text.endswith('```'):
                text = text[len('```text'):-3].strip()
            if not text:
                raise ValueError('Empty final natural-language submission')
            return dict(history=history, ready=dict(action='ready', question='', text=text,
                        removed_goals=[], complete_request_decision='unknown',
                        reason='BIRD terminal submission'), forced_submission=turn==max_turns+1)
        if turn == max_turns + 1:
            return dict(history=history, budget_exhausted=True, last_action=response)
        if '</s>' not in response or not text.strip():
            raise ValueError('Invalid upstream ask/submit protocol')
        question = dict(question=text, options=[], multi_select=False)
        answer = simulator(call, source['question'], question, history,
                           source.get('flat_units', []), source['reference_sql'])
        history.append(dict(question=question, answer=answer))
        public_answer = answer['text']
        if answer['action'] == 'unanswerable' and not public_answer:
            public_answer = 'Sorry, this question is out of scope, so I can not answer your question.'
        user['case'] = {'prediction_turn_' + str(turn): '<s>' + public_answer + '</s>'}
    raise AssertionError('Unreachable')


class TextTransport:
    def __init__(self, recorder, record, path, max_tokens):
        self.recorder, self.record, self.path = recorder, record, path
        self.max_tokens = max_tokens
        self.json_call = recorder.bind(record, path, 0, 'bird_conv_nl_v2')

    def __call__(self, stage, prompt, payload):
        if stage != 'baseline_bird_conv_nl':
            return self.json_call(stage, prompt, payload)
        from clarification_deferred_deletion.baseline import module
        recorder = self.recorder
        if recorder.attempted >= recorder.max_calls:
            raise RuntimeError('API attempt budget reached')
        recorder.attempted += 1
        options = recorder.request_options(stage)
        messages = [{'role': 'user', 'content': prompt}]
        entry = dict(stage=stage, round=0, arm='bird_conv_nl_v2', messages=deepcopy(messages),
                     request_options=dict(options, temperature=0, max_tokens=self.max_tokens,
                                          top_p=1, frequency_penalty=0, presence_penalty=0),
                     response_attempt=1, serialization='upstream_plain_text')
        self.record['calls'].append(entry)
        atomic = module('storage').atomic_json
        atomic(self.path, self.record)
        start = time.perf_counter()
        try:
            response = recorder.client.chat.completions.create(
                model=recorder.model, messages=messages, temperature=0,
                max_tokens=self.max_tokens, top_p=1, frequency_penalty=0, presence_penalty=0,
                **options)
            if response is None:
                raise RuntimeError('Provider returned no response')
            entry.update(response=response.model_dump(mode='json'),
                         usage=response.usage.model_dump() if response.usage else {})
            content = response.choices[0].message.content
            if not isinstance(content, str) or not content.strip():
                raise ValueError('Empty upstream text response')
            if response.choices[0].finish_reason == 'length':
                raise ValueError('Upstream text response truncated at token limit')
            entry['response_status'] = 'text_received'
            return content
        except Exception as exc:
            entry.update(response_status='failed', error_type=type(exc).__name__)
            raise
        finally:
            entry['elapsed_seconds'] = time.perf_counter() - start
            atomic(self.path, self.record)


def provenance():
    return dict(reference_commit=UPSTREAM_COMMIT,
                upstream_prompt_sha256=shared.sha(UPSTREAM_PROMPT),
                upstream_builder_sha256=shared.sha(ROOT / 'BIRD-Interact/bird_interact_conv/code/infer_api_system.py'),
                adapted_prompt_sha256=hashlib.sha256(PROMPT.encode('utf8')).hexdigest(),
                substitutions=SUBSTITUTIONS,
                shared_environment=['requester_protocol_v6_one_stage_gold', 'fixed answer/call budget',
                                    'empty unanswerable rendered with original decoder refusal example'],
                data_adaptation=['frozen schema JSON', 'no external knowledge supplied'],
                terminal_policy='upstream final-round forced submission; completion is not correctness',
                api_policy='upstream user-only plain text, temperature=0, default max_tokens=512; bounded failures')


def export(out, cases):
    summary = shared.export(out, cases)
    rows = shared.jsonl(out / 'evaluation_rows.jsonl')
    turns = Counter()
    by_level = {}
    for row in rows:
        trace = shared.read(out / row['trace'])
        n = len(trace.get('history', []))
        row.update(profile=VERSION, user_turns=n)
        bucket = '0' if n == 0 else '1' if n == 1 else '2+'
        turns[bucket] += 1
        level = by_level.setdefault(str(row['level']),
                                   dict(recorded=0, user_turns_total=0,
                                        turn_buckets={'0': 0, '1': 0, '2+': 0}, outcomes={}))
        level['recorded'] += 1
        level['user_turns_total'] += n
        level['turn_buckets'][bucket] += 1
        level['outcomes'][row['outcome']] = level['outcomes'].get(row['outcome'], 0) + 1
    (out / 'evaluation_rows.jsonl').write_text(
        ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf8')
    summary.update(profile=VERSION, user_turn_buckets=dict(turns), by_level=by_level)
    (out / 'SUMMARY.json').write_text(json.dumps(summary, indent=2), encoding='utf8')
    return summary


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--fixture', type=Path, default=shared.FIXTURE)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--model', default='deepseek-flash')
    p.add_argument('--turns', type=int, default=30)
    p.add_argument('--agent-max-tokens', type=int, default=512)
    p.add_argument('--max-calls', type=int, default=80)
    p.add_argument('--pilot', action='store_true')
    p.add_argument('--limit', type=int)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--check-only', action='store_true')
    args = p.parse_args()
    if args.agent_max_tokens < 1 or args.turns < 0 or args.max_calls < 1 or (args.limit is not None and args.limit < 1):
        p.error('Invalid budget or limit')
    cases = shared.prepare(args)
    from clarification_deferred_deletion.baseline import module
    cfg = dict(version=VERSION, strategy='serial', profile='bird_conv_nl_v2', agent_max_tokens=args.agent_max_tokens,
               model=args.model, turns=args.turns, max_calls=args.max_calls, pilot=args.pilot,
               ids=[x[0]['id'] for x in cases], runner_sha256=shared.sha(Path(__file__)),
               shared_runner_sha256=shared.sha(Path(shared.__file__)),
               api_adapter_sha256=shared.sha(ROOT / 'scripts/run_clarification_deepseek.py'),
               fixture_sha256=shared.sha(args.fixture / 'selection.json'),
               shared_runtime_sha256=module('runtime').runtime_hashes(),
               authorization_stage='sql_backend', execution_feedback=False,
               simulator='requester_protocol_v6_one_stage_gold', provenance=provenance())
    if args.check_only:
        print(json.dumps(dict(validated=len(cases), profile=VERSION,
                              counts=dict(Counter(x[0]['dataset'] + ':' + x[0]['original_permission'] for x in cases)),
                              provenance=cfg['provenance'])))
        return
    args.out.mkdir(parents=True, exist_ok=True)
    atomic = module('storage').atomic_json
    if args.resume:
        if shared.read(args.out / 'CONFIG.json') != cfg:
            p.error('Resume configuration differs; use the original code or a new directory')
    elif any(args.out.iterdir()):
        p.error('Output must be empty, or use --resume')
    else:
        atomic(args.out / 'CONFIG.json', cfg)
    if args.model == 'deepseek-flash':
        from scripts.run_clarification_deepseek import configure, install_v4_non_thinking
        configure()
        install_v4_non_thinking(module('recorder').Recorder)
    else:
        module('environment').load_dotenv(ROOT / 'clarification_main/.env')
    done = 0
    for audit, c, source, context in cases:
        path = args.out / (str(c['question_id']) + '_' + str(c['level']) + '_baseline.json')
        if path.exists():
            continue
        if args.limit is not None and done >= args.limit:
            break
        rec = dict(id=audit['id'], question_id=c['question_id'], level=c['level'],
                   strategy='serial', profile=VERSION, model=args.model, calls=[],
                   history=[], outcome='running')
        atomic(path, rec)
        try:
            recorder = module('recorder').Recorder(args.model, args.max_calls, [],
                                                   timeout=120, empty_retries=1, transport_retries=1)
            bound = TextTransport(recorder, rec, path, args.agent_max_tokens)
            def simulator(*sim_args):
                answer = module('engine.simulator').simulate_requester_with_sql(*sim_args)
                rec['history'].append(dict(question=sim_args[2], answer=answer))
                atomic(path, rec)
                return answer
            payload = dict(request=source['levels'][str(c['level'])]['rewritten_request'],
                           schema=context, database=c['db_id'])
            result = dialogue(payload, source, bound, simulator, args.turns)
            rec['history'] = result['history']
            rec['budget_exhausted'] = bool(result.get('budget_exhausted'))
            rec['forced_submission'] = bool(result.get('forced_submission'))
            if result.get('last_action'):
                rec['last_action'] = result['last_action']
            final = shared.frontend_delivery('serial', result)
            if final:
                rec.update(delivery=final, outcome='deliver')
            else:
                rec['outcome'] = 'unresolved'
        except Exception as exc:
            import os
            message = str(exc)
            key = os.getenv('OPENAI_API_KEY')
            if key:
                message = message.replace(key, '<redacted>')
            rec.update(outcome='system_error', error_type=type(exc).__name__, error=message)
        rec['cost'] = module('recorder').cost(rec['calls'])
        rec['agent_cost'] = module('recorder').cost([x for x in rec['calls'] if x['stage'].startswith('baseline_')])
        rec['simulator_cost'] = module('recorder').cost([x for x in rec['calls'] if not x['stage'].startswith('baseline_')])
        atomic(path, rec)
        done += 1
        print('FINISHED', VERSION, c['question_id'], c['level'], rec['outcome'], flush=True)
    print(json.dumps(export(args.out, cases)))


if __name__ == '__main__':
    main()
