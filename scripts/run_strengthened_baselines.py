"""Original serial and joint prompt controls on the frozen paired cohort."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURE = ROOT / 'artifacts/clarification_main/manual_flat_gold_v1_instruction_v10_preflight_20261101'
BASE = ROOT / 'artifacts/main_analysis_server_20261102'
VERSION = 'interactive_prompt_controls_v2_deferred_serial_authorization'
COMMON = '''Resolve a business database request using only its public request,
schema and dialogue. Ask focused open-ended questions when ambiguity affects SQL.
Do not invent intent or use database execution, data samples or reference SQL.
Return JSON with exactly: action, question, text, removed_goals,
complete_request_decision, reason. All are strings except removed_goals, an array
of goal descriptions. action: ask/ready/deliver/refuse. Decision: unknown/allowed/denied.
ask requires nonempty question, empty text/removals and unknown decision.
ready requires nonempty clarified text, empty removals and unknown decision.
deliver requires nonempty clarified remaining business request, not SQL.
refuse requires empty text and denied decision. Terminal decisions cannot be unknown.
Any removal means the complete original request is denied. State removals explicitly.
Unanswerable is not consent. Preserve necessary filters, joins and aggregates.'''
CLARIFY = COMMON + ''' Clarification-only stage: use ask or ready. Permissions
are unavailable at this stage. Do not remove goals or decide authorization.'''
POLICY = ''' Allowed_columns is the effective read allowlist; every other column
is forbidden, including predicates, joins, aggregates, ordering and subqueries.
If partly restricted, preserve meaningful authorized goals, removing restricted
goals and dependent goals. Do not delete necessary conditions to make output legal.
Refuse if no meaningful authorized remainder exists. Do not ask to violate policy.'''
JOINT = COMMON + POLICY + ''' Jointly consider ambiguity and permissions each turn.
Use ask, deliver or refuse; never ready. Clarify, prune or deliver as appropriate.'''


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path):
    return [json.loads(x) for x in path.read_text(encoding='utf8').splitlines() if x.strip()]


def validate(raw, actions):
    keys = {'action', 'question', 'text', 'removed_goals', 'complete_request_decision', 'reason'}
    if not isinstance(raw, dict) or set(raw) != keys or raw['action'] not in actions:
        raise ValueError('Invalid agent response schema/action')
    if any(not isinstance(raw[k], str) for k in keys - {'removed_goals'}):
        raise ValueError('Agent fields must be strings')
    if not isinstance(raw['removed_goals'], list) or any(not isinstance(x, str) or not x.strip() for x in raw['removed_goals']):
        raise ValueError('Invalid removals')
    a, d = raw['action'], raw['complete_request_decision']
    if d not in {'unknown', 'allowed', 'denied'}:
        raise ValueError('Invalid decision')
    if a == 'ask' and (not raw['question'].strip() or raw['text'] or raw['removed_goals'] or d != 'unknown'):
        raise ValueError('Invalid question')
    if a == 'ready' and (not raw['text'].strip() or raw['removed_goals'] or d != 'unknown'):
        raise ValueError('Invalid ready response')
    if a in {'deliver', 'refuse'} and (d == 'unknown' or (raw['removed_goals'] and d != 'denied')):
        raise ValueError('Invalid terminal decision')
    if a == 'deliver' and not raw['text'].strip():
        raise ValueError('Empty delivery')
    if a == 'refuse' and (raw['text'] or d != 'denied'):
        raise ValueError('Invalid refusal')
    return raw


def agent_call(call, stage, prompt, payload, actions):
    # Match the main runtime's one contract-repair attempt; budgeted and logged.
    from clarification_deferred_deletion.baseline import module
    return module('engine.analysis').validated_call(call,stage,prompt,payload,
                                                   lambda raw:validate(raw,actions))


def dialogue(strategy, payload, source, call, simulator, max_turns):
    history = []
    state = dict(payload, public_history=[])
    for turn in range(max_turns + 1):
        prompt, actions = (CLARIFY, {'ask', 'ready'}) if strategy == 'serial' else (JOINT, {'ask', 'deliver', 'refuse'})
        raw = agent_call(call,'baseline_' + strategy,prompt,state,actions)
        if raw['action'] == 'ready':
            state['clarified_request'] = raw['text']
            return dict(ready=raw, history=history, state=state)
        if raw['action'] in {'deliver', 'refuse'}:
            return dict(final=raw, history=history)
        if turn == max_turns:
            return dict(history=history, budget_exhausted=True)
        question = dict(question=raw['question'], options=[], multi_select=False)
        answer = simulator(call, source['question'], question, history,
                           source.get('flat_units', []), source['reference_sql'])
        history.append(dict(question=question, answer=answer))
        # Private module IDs and answer basis never become agent inputs.
        public = {k: answer[k] for k in ('action', 'text', 'choices')}
        state['public_history'].append(dict(question=question, answer=public))


def frontend_delivery(strategy, result):
    if strategy == 'serial' and 'ready' in result:
        # Delivery here means ready for backend generation, not authorized.
        return dict(result['ready'], action='deliver',
                    complete_request_decision='unknown', removed_goals=[],
                    authorization_deferred=True)
    return result.get('final')


def prepare(args):
    selection = read(args.fixture / 'selection.json')
    for name, expected in selection['fixture_payload_sha256'].items():
        if sha(args.fixture / name) != expected:
            raise ValueError('Fixture hash differs: ' + name)
    cohort = [r for r in jsonl(BASE/'PRUNING_DECISION_AUDIT.jsonl') if r['outcome'] in {'deliver', 'refuse'}]
    digest = hashlib.sha256(chr(10).join(sorted(r['id'] for r in cohort)).encode()).hexdigest()
    if len(cohort) != 2800 or digest != read(BASE/'paired_evaluation/PAIRING_MANIFEST.json')['cohort_ids_sha256']:
        raise ValueError('Frozen cohort changed')
    if args.pilot:
        buckets = {}
        for r in cohort:
            buckets.setdefault((r['dataset'], r['original_permission'], r['level']), []).append(r)
        cohort = [min(v, key=lambda r: hashlib.sha256(('20261103:'+r['id']).encode()).hexdigest()) for k,v in sorted(buckets.items())]
    cases = {(c['question_id'], c['level']): c for c in selection['cases']}
    sources, schemas = read(args.fixture/'sources.json'), read(args.fixture/'schemas.json')
    roles = read(args.fixture/'roles.json')
    from clarification_deferred_deletion.baseline import module
    live = module('live')
    loaded = []
    for row in cohort:
        c = cases[row['question_id'], row['level']]
        s = sources[str(row['question_id'])]
        role = next(r for r in roles if r['db_id']==c['db_id'] and r['role']==c['role'] and r['metadata']['question_id']==c['policy_source_question_id'])
        context = live.schema_context(schemas[c['db_id']])
        allowed = live.engine_module('policy').allowed_columns(context, role['policy'])
        identity = ':'.join(str(c[k]) for k in ('question_id','level','role','policy_sha256'))
        policy_sha = hashlib.sha256(json.dumps(role['policy'], sort_keys=True).encode()).hexdigest()
        if row['id'] != identity or s['source_sha256'] != c['source_sha256'] or set(allowed) != set(c['allowed_columns']) or policy_sha != c['policy_sha256']:
            raise ValueError('Identity/policy mismatch')
        loaded.append((row, c, s, context))
    return loaded


def generation_inputs(rows):
    # Only public natural language and identity keys travel to the SQL backend.
    return [dict(id=r['id'],question_id=r['question_id'],level=r['level'],
                 dataset=r['dataset'],db_id=r['db_id'],role=r['role'],
                 clarified_text=r['clarified_text'])
            for r in rows if r['outcome']=='deliver' and r['clarified_text'].strip()]


def export(out, cases):
    rows = []
    for audit, c, source, context in cases:
        path = out / (str(c['question_id'])+'_'+str(c['level'])+'_baseline.json')
        if not path.is_file():
            continue
        t = read(path)
        final = t.get('delivery', {})
        rows.append(dict(id=audit['id'], question_id=c['question_id'], level=c['level'], db_id=c['db_id'], role=c['role'], dataset=c['dataset'],
            original_permission=audit['original_permission'], policy_sha256=c['policy_sha256'], outcome=t['outcome'], clarified_text=final.get('text',''),
            has_pruning=bool(final.get('removed_goals')), removed_goals=final.get('removed_goals',[]),
            complete_request_decision=final.get('complete_request_decision','unknown'),
            authorization_deferred=t.get('strategy')=='serial',
            frontend_only=True, allowed_columns=c['allowed_columns'], cost=t.get('cost'), trace=path.name))
    (out/'evaluation_rows.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+chr(10) for r in rows),encoding='utf8')
    inputs=generation_inputs(rows)
    (out/'clarified_requests.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+chr(10) for r in inputs),encoding='utf8')
    summary = dict(planned=len(cases), recorded=len(rows),
                   clarified_requests=len(inputs),outcomes=dict(Counter(r['outcome'] for r in rows)))
    (out/'SUMMARY.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    return summary


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--strategy',choices=['serial','joint'],required=True)
    p.add_argument('--fixture',type=Path,default=FIXTURE)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--model',default='deepseek-flash')
    p.add_argument('--turns',type=int,default=30)
    p.add_argument('--max-calls',type=int,default=80)
    p.add_argument('--pilot',action='store_true')
    p.add_argument('--limit',type=int)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--check-only',action='store_true')
    args = p.parse_args()
    if args.turns<0 or args.max_calls<1 or (args.limit is not None and args.limit<1):
        p.error('Invalid budget or limit')
    cases = prepare(args)
    from clarification_deferred_deletion.baseline import module
    cfg = dict(version=VERSION,strategy=args.strategy,model=args.model,turns=args.turns,max_calls=args.max_calls,pilot=args.pilot,
        ids=[x[0]['id'] for x in cases],runner_sha256=sha(Path(__file__)),fixture_sha256=sha(args.fixture/'selection.json'),
        shared_runtime_sha256=module('runtime').runtime_hashes(),
        authorization_stage='sql_backend' if args.strategy=='serial' else 'joint_frontend_and_sql_audit',
        execution_feedback=False,simulator='requester_protocol_v6_one_stage_gold')
    if args.check_only:
        print(json.dumps(dict(validated=len(cases),counts=dict(Counter(x[0]['dataset']+':'+x[0]['original_permission'] for x in cases)))))
        return
    if args.model=='deepseek-flash':
        from scripts.run_clarification_deepseek import configure,install_v4_non_thinking
        configure()
        install_v4_non_thinking(module('recorder').Recorder)
    else:
        module('environment').load_dotenv(ROOT/'clarification_main/.env')
    args.out.mkdir(parents=True,exist_ok=True)
    atomic = module('storage').atomic_json
    if args.resume:
        if read(args.out/'CONFIG.json')!=cfg:
            p.error('Resume configuration differs')
    elif any(args.out.iterdir()):
        p.error('Output must be empty, or use --resume')
    else:
        atomic(args.out/'CONFIG.json',cfg)
    done=0
    for audit,c,source,context in cases:
        path=args.out/(str(c['question_id'])+'_'+str(c['level'])+'_baseline.json')
        if path.exists():
            continue
        if args.limit is not None and done>=args.limit:
            break
        rec=dict(id=audit['id'],question_id=c['question_id'],level=c['level'],strategy=args.strategy,model=args.model,calls=[],outcome='running')
        atomic(path,rec)
        try:
            recorder=module('recorder').Recorder(args.model,args.max_calls,[],timeout=120,empty_retries=1,transport_retries=1)
            call=recorder.bind(rec,path,0,args.strategy)
            payload=dict(request=source['levels'][str(c['level'])]['rewritten_request'],schema=context,database=c['db_id'])
            if args.strategy=='joint':
                payload['allowed_columns']=c['allowed_columns']
            result=dialogue(args.strategy,payload,source,call,module('engine.simulator').simulate_requester_with_sql,args.turns)
            rec['history']=result['history']
            final=frontend_delivery(args.strategy,result)
            if final:
                rec.update(delivery=final,outcome=final['action'])
            else:
                rec['outcome']='unresolved'
        except Exception as exc:
            import os
            rec.update(outcome='system_error',error_type=type(exc).__name__,error=str(exc).replace(os.getenv('OPENAI_API_KEY','<no-key>'),'<redacted>'))
        rec['cost']=module('recorder').cost(rec['calls'])
        rec['agent_cost']=module('recorder').cost([x for x in rec['calls'] if x['stage'].startswith('baseline_')])
        rec['simulator_cost']=module('recorder').cost([x for x in rec['calls'] if not x['stage'].startswith('baseline_')])
        atomic(path,rec)
        done+=1
        print('FINISHED',args.strategy,c['question_id'],c['level'],rec['outcome'],flush=True)
    print(json.dumps(export(args.out,cases)))


if __name__=='__main__':
    main()
