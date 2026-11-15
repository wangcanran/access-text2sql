"""Auditable source-based generation with bounded correction attempts."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import random

from openai import OpenAI
from clause_frame_experiment.model import parse_query_frame
from intent_refinement_bird_v2.llm_client import load_dotenv
from request_disclosure_experiment.run_smoke import schema_context
from request_disclosure_experiment.disclosure import build_inventory
from .prompts import DISCLOSURE_FRAME_PROMPT, INVENTORY_PROMPT, ROLE_PROMPT, OBLIGATION_PROMPT, DIRECTION_PROMPT, DIRECTION_REPAIR_PROMPT, REWRITE_PROMPT, REPLACEMENT_PROMPT, REVIEW_PROMPT
from .storage import atomic_json
from .pipeline import apply_roles, counted_items, normalize_shared_operators, plan_levels, source_ledger, protected_ledger, protected_text_issues, replacement_semantic_issues, apply_replacements, requires_clause_rewrite, source_span_diagnostics, validate_review

ROOT = Path(__file__).resolve().parents[1]


def select_flat(frame, fraction, seed):
    if not 0 < fraction <= 1:
        raise ValueError('fraction must be in (0,1]')
    items = list(frame.outputs + frame.modifiers)
    count = math.ceil(len(items) * fraction)
    chosen = set(random.Random(seed).sample(range(len(items)), count))
    return [x['item_id'] for i, x in enumerate(items) if i in chosen]


def validate_directions(raw, selected, frame=None):
    if not isinstance(raw, dict) or set(raw) != {'directions'} or not isinstance(raw['directions'], list):
        raise ValueError('expected directions array')
    seen = []
    normalized = []
    for row in raw['directions']:
        if not isinstance(row, dict) or set(row) not in (
                {'item_id','action','source_specification','direction','retain'},
                {'item_id','action','direction','retain'}):
            raise ValueError('invalid direction fields')
        row = dict(row)
        if 'source_specification' not in row:
            if frame is None:
                raise ValueError('source frame required to bind specification')
            spans = {i['item_id']:i['span'] for i in frame.outputs + frame.modifiers}
            if row.get('item_id') not in spans:
                raise ValueError('unknown source item')
            row['source_specification'] = spans[row['item_id']] if row['action'] == 'hide' else ''
        if any(not isinstance(v,str) for v in row.values()):
            raise ValueError('direction values must be strings')
        if row['action'] not in ('hide','context_only') or not row['direction'].strip() or not row['retain'].strip():
            raise ValueError('invalid direction')
        if (row['action']=='hide') != bool(row['source_specification'].strip()):
            raise ValueError('original answer required only for hide')
        if frame is not None and row['action'] == 'hide':
            spans = {i['item_id']: i['span'] for i in frame.outputs + frame.modifiers}
            if row['item_id'] not in spans or row['source_specification'] not in spans[row['item_id']]:
                raise ValueError('original answer must be quoted from its source item')
        seen.append(row['item_id'])
        normalized.append(row)
    if len(seen) != len(set(seen)) or set(seen) != set(selected):
        raise ValueError('directions must cover exactly selected IDs')
    return {'directions':normalized}


def validate_obligations(raw, frame):
    if not isinstance(raw, dict) or set(raw) != {'obligations'} or not isinstance(raw['obligations'], list):
        raise ValueError('expected obligations array')
    expected = {item['item_id'] for item in frame.outputs + frame.modifiers}
    seen = set()
    for row in raw['obligations']:
        if (not isinstance(row, dict) or set(row) != {'item_id', 'requirement'}
                or not isinstance(row['item_id'], str) or row['item_id'] not in expected
                or row['item_id'] in seen or not isinstance(row['requirement'], str)
                or not row['requirement'].strip()):
            raise ValueError('invalid source obligation')
        seen.add(row['item_id'])
    if seen != expected:
        raise ValueError('obligations must cover every source item')
    return raw


def run_case(case, args, schemas, out, ratios=None, fixed_ids=None, response_cache=None):
    trace = {**case,'calls':[],'selection_fraction':args.fraction,'seed':args.seed,'rewrite_mode':args.rewrite_mode,
             'review_model':args.review_model or args.model,
             'note':'Fragment fraction, not semantic disclosure percentage; no gold SQL/evidence sent.'}
    path = out/f"{case['question_id']}.json"
    def save(): atomic_json(path, trace)
    client = OpenAI(api_key=os.environ['OPENAI_API_KEY'],base_url=os.getenv('OPENAI_BASE_URL'),timeout=120,max_retries=1)
    def call(stage, prompt, payload=None):
        messages = [{'role':'system','content':prompt}]
        if payload is not None: messages.append({'role':'user','content':json.dumps(payload,ensure_ascii=False)})
        record = {'stage':stage,'request':{'model':args.review_model if stage.startswith('review_') and args.review_model else args.model,
                   'temperature':0,'response_format':{'type':'json_object'},'messages':messages}}
        trace['calls'].append(record); save()
        def invoke():
            return client.chat.completions.create(**record['request']).model_dump(mode='json')
        response = response_cache(stage, record['request'], invoke) if response_cache else invoke()
        record['response'] = response; save()
        return json.loads(response['choices'][0]['message']['content'])
    try:
        context = schema_context(schemas[case['db_id']])
        prompt = DISCLOSURE_FRAME_PROMPT.replace('[[DB_name]]',case['db_id']).replace('[[db_schema]]',json.dumps({k:context[k] for k in ('tables','foreign_keys')},ensure_ascii=False)).replace('[[data_description]]',json.dumps(context['column_language'],ensure_ascii=False)).replace('[[user_request]]',case['question'])
        if args.replay_frames:
            prior=json.loads((args.replay_frames/f"{case['question_id']}.json").read_text(encoding='utf8'))
            raw=prior['frame']
            trace['frame_replay_source']=str(args.replay_frames/f"{case['question_id']}.json")
        else:
            raw = call('frame',prompt)
        trace['frame']=raw; save()
        frame = parse_query_frame(raw)
        if frame.original_text != case['question']: raise ValueError('original changed')
        try:
            trace['source_inventory'] = build_inventory(frame)
            if not frame.outputs and not frame.modifiers:
                raise ValueError('no source specifications; cannot generate disclosure levels')
        except ValueError as exc:
            if args.replay_frames: raise
            raw = call('frame_contract_retry',prompt,{'previous_response':raw,
                'contract_error':str(exc),
                'source_position_issues':source_span_diagnostics(frame),
                'correction':'Return independent non-overlapping source spans. A parent and its children cannot both be counted. Preserve the specifications once, not duplicated.'})
            trace['frame']=raw; save()
            frame=parse_query_frame(raw)
            if frame.original_text != case['question']: raise ValueError('original changed')
            trace['source_inventory'] = build_inventory(frame)
        if not frame.outputs and not frame.modifiers:
            raise ValueError('no source specifications; cannot generate disclosure levels')
        roles = call('roles', ROLE_PROMPT, {'frame':raw})
        try:
            checked_frame, role_changes = apply_roles(frame, roles)
        except ValueError as exc:
            roles = call('roles_contract_retry', ROLE_PROMPT,
                {'frame':raw,'previous_response':roles,'contract_error':str(exc)})
            checked_frame, role_changes = apply_roles(frame, roles)
        trace['role_review'] = roles
        if role_changes:
            trace['frame_model_output'] = raw
            trace['role_changes'] = role_changes
            frame = checked_frame
            raw = {'original_text':frame.original_text,
                   'outputs':list(frame.outputs),'modifiers':list(frame.modifiers)}
            trace['frame'] = raw
            trace['source_inventory'] = build_inventory(frame)
        save()
        required_item_ids = [item['item_id'] for item in frame.outputs + frame.modifiers]
        obligations = call('obligations', OBLIGATION_PROMPT,
            {'frame':raw,'required_item_ids':required_item_ids})
        try:
            validate_obligations(obligations, frame)
        except ValueError as exc:
            obligations = call('obligations_contract_retry', OBLIGATION_PROMPT,
                {'frame':raw,'required_item_ids':required_item_ids,
                 'previous_response':obligations,'contract_error':str(exc)})
            validate_obligations(obligations, frame)
        trace['source_obligations'] = obligations; save()
        frame, normalization = normalize_shared_operators(frame, obligations)
        if normalization:
            trace['frame_before_operator_normalization'] = raw
            trace['frame_normalization'] = normalization
            raw = {'original_text':frame.original_text,
                   'outputs':list(frame.outputs),'modifiers':list(frame.modifiers)}
            trace['frame'] = raw
            trace['source_inventory'] = build_inventory(frame)
            save()
        inventory = call('inventory',INVENTORY_PROMPT,{'frame':raw})
        trace['counting_inventory']=inventory; save()
        try:
            items = counted_items(frame, inventory)
        except ValueError as exc:
            inventory = call('inventory_contract_retry',INVENTORY_PROMPT,
                {'frame':raw,'previous_response':inventory,'contract_error':str(exc)})
            trace['counting_inventory']=inventory; save()
            items = counted_items(frame, inventory)
        if not items: raise ValueError('no independently counted specifications')
        all_ids = [i['item_id'] for i in frame.outputs+frame.modifiers]
        if fixed_ids is not None:
            if not set(fixed_ids) <= {i['item_id'] for i in items}:
                raise ValueError('fixed selected IDs must belong to counted frame items')
            selected = list(fixed_ids)
            trace['selection_override'] = selected
            levels = [{'level':0,'selected_ids':selected,'retained_ids':[i for i in all_ids if i not in selected]}]
        elif ratios is None:
            chosen = set(random.Random(args.seed).sample(range(len(items)),math.ceil(len(items)*args.fraction)))
            selected = [i['item_id'] for n,i in enumerate(items) if n in chosen]
            levels = [{'level':0,'selected_ids':selected,'retained_ids':[i for i in all_ids if i not in selected]}]
        else:
            trace['disclosure_plan'] = plan_levels(frame,ratios,args.seed,inventory)
            levels = trace['disclosure_plan']['levels']
            for level in levels:
                level['retained_ids'] = [i for i in all_ids if i not in level['selected_ids']]
        trace['levels'] = []; cache = {}
        for plan in levels:
            row = {**plan,'source_specifications':source_ledger(frame,plan['selected_ids']),
                   'protected_fragments':protected_ledger(frame,plan['selected_ids'])}
            trace['levels'].append(row); save()
            key = tuple(plan['selected_ids'])
            if key in cache:
                row.update(cache[key]); save(); continue
            if not key:
                row.update(rewritten_request=frame.original_text,status='source_identity',
                           active_hidden_fragments=0,effective_retention_ratio=1.0)
                cache[key] = {k:row[k] for k in ('rewritten_request','status','active_hidden_fragments','effective_retention_ratio')}; save(); continue
            direction_payload = {
                'frame':raw,'selected_ids':plan['selected_ids'],
                **obligations,
                'protected_fragments':row['protected_fragments']}
            directions = call(f"directions_{plan['level']}",DIRECTION_PROMPT,direction_payload)
            row['directions']=directions; save()
            try:
                directions = validate_directions(directions,plan['selected_ids'],frame)
            except ValueError as exc:
                directions = call(f"directions_contract_retry_{plan['level']}",DIRECTION_PROMPT,
                    {**direction_payload,'previous_response':directions,'contract_error':str(exc),
                     'correction':'Every entry needs item_id, action, direction, retain. Source specifications are supplied by code; retain also applies to context_only.'})
                row['directions']=directions; save()
                directions = validate_directions(directions,plan['selected_ids'],frame)
            row['directions']=directions; save()
            active = sum(d['action']=='hide' for d in directions['directions'])
            row['active_hidden_fragments'] = active
            row['effective_retention_ratio'] = 1-active/len(items)
            row['attempts'] = []
            row['direction_revisions'] = []
            clause_rewrite = args.rewrite_mode == 'local' and requires_clause_rewrite(frame,directions)
            if clause_rewrite:
                row['rewrite_route'] = 'whole: hidden count question and predicate flank a fixed copula'
                save()
            feedback = None
            for attempt in range(args.repair_attempts+1):
                protected = protected_ledger(frame,[d['item_id'] for d in directions['directions'] if d['action']=='hide'])
                payload = {'frame':raw,**obligations,**directions,'protected_fragments':protected}
                hide_ids = {d['item_id'] for d in directions['directions'] if d['action']=='hide'}
                payload['replacement_targets'] = [
                    {'item_id':i['item_id'],'source_span':i['span'],
                     'left_context':frame.original_text[:i['start']],
                     'right_context':frame.original_text[i['end']:]}
                    for i in trace['source_inventory']['items'] if i['item_id'] in hide_ids]
                if feedback is not None: payload['previous_attempt_review'] = feedback
                if args.rewrite_mode == 'local' and not clause_rewrite:
                    replacement = call(f"replacement_{plan['level']}_{attempt}",REPLACEMENT_PROMPT,payload)
                    row.setdefault('replacement_attempts',[]).append(replacement); save()
                    try:
                        rewritten = apply_replacements(frame,directions,replacement)
                    except ValueError as exc:
                        replacement = call(f"replacement_contract_retry_{plan['level']}_{attempt}",REPLACEMENT_PROMPT,
                            {**payload,'previous_response':replacement,'contract_error':str(exc),
                             'required_replacement_ids':sorted(hide_ids)})
                        row['replacement_attempts'].append(replacement); save()
                        rewritten = apply_replacements(frame,directions,replacement)
                else:
                    rewritten = call(f"rewrite_{plan['level']}_{attempt}",REWRITE_PROMPT,payload)
                edits = rewritten.pop('edits',None)
                if not isinstance(rewritten,dict) or set(rewritten)!={'rewritten_request'} or not isinstance(rewritten['rewritten_request'],str) or not rewritten['rewritten_request'].strip():
                    raise ValueError('invalid rewrite')
                record = {**rewritten,'protection_issues':protected_text_issues(rewritten['rewritten_request'],protected)}
                if edits is not None: record['edits'] = edits
                record['semantic_issues'] = replacement_semantic_issues(
                    edits or [], rewritten['rewritten_request'])
                row['attempts'].append(record); save()
                review_payload = {'frame':raw,**obligations,**rewritten,
                                  'hide_ids':sorted(hide_ids),
                                  'protected_ids':[i['item_id'] for i in frame.outputs+frame.modifiers
                                                   if i['item_id'] not in hide_ids]}
                for review_attempt in range(2):
                    review = call(f"review_{plan['level']}_{attempt}_{review_attempt}",REVIEW_PROMPT,review_payload)
                    record.setdefault('review_attempts',[]).append(review); save()
                    try:
                        passed = validate_review(review,directions,plan['retained_ids'])
                        break
                    except ValueError as exc:
                        if review_attempt: raise
                        review_payload = {**review_payload,'format_correction':str(exc),
                                          'required_item_ids':[i['item_id'] for i in frame.outputs+frame.modifiers]}
                record['review']=review; save()
                passed = passed and not record['protection_issues'] and not record['semantic_issues']
                row.update(**rewritten,status='review_passed' if passed and active else 'needs_review')
                if passed:
                    if clause_rewrite: row['accepted_mode'] = 'whole_clause'
                    break
                feedback = {**rewritten,**review,'protection_issues':record['protection_issues'],
                            'semantic_issues':record['semantic_issues']}
                if attempt < args.repair_attempts:
                    repair_payload = {'frame':raw,**obligations,'selected_ids':plan['selected_ids'],
                                      'protected_fragments':row['protected_fragments'],
                                      'previous_directions':directions,'failed_attempt':feedback}
                    revised = call(f"review_directions_repair_{plan['level']}_{attempt}",
                                   DIRECTION_REPAIR_PROMPT,repair_payload)
                    revised = validate_directions(revised,plan['selected_ids'],frame)
                    row['direction_revisions'].append(revised)
                    directions = revised
                    row['directions'] = directions
                    active = sum(d['action']=='hide' for d in directions['directions'])
                    row['active_hidden_fragments'] = active
                    row['effective_retention_ratio'] = 1-active/len(items)
                    clause_rewrite = args.rewrite_mode == 'local' and requires_clause_rewrite(frame,directions)
                    if clause_rewrite:
                        row['rewrite_route'] = 'whole: hidden count question and predicate flank a fixed copula'
                    else:
                        row.pop('rewrite_route',None)
                    save()
            if args.rewrite_mode == 'local' and not clause_rewrite and active and row['status'] == 'needs_review':
                protected = protected_ledger(frame,[d['item_id'] for d in directions['directions'] if d['action']=='hide'])
                hide_ids = {d['item_id'] for d in directions['directions'] if d['action']=='hide'}
                fallback_payload = {'frame':raw,**obligations,**directions,
                                    'protected_fragments':protected,
                                    'previous_attempt_review':feedback}
                rewritten = call(f"rewrite_fallback_{plan['level']}",REWRITE_PROMPT,fallback_payload)
                if (not isinstance(rewritten,dict) or set(rewritten) != {'rewritten_request'}
                        or not isinstance(rewritten['rewritten_request'],str)
                        or not rewritten['rewritten_request'].strip()):
                    raise ValueError('invalid fallback rewrite')
                record = {**rewritten,'mode':'whole_fallback',
                          'protection_issues':protected_text_issues(rewritten['rewritten_request'],protected),
                          'semantic_issues':[]}
                row['attempts'].append(record); save()
                review_payload = {'frame':raw,**obligations,**rewritten,
                                  'hide_ids':sorted(hide_ids),
                                  'protected_ids':[i['item_id'] for i in frame.outputs+frame.modifiers
                                                   if i['item_id'] not in hide_ids]}
                for review_attempt in range(2):
                    review = call(f"review_fallback_{plan['level']}_{review_attempt}",
                                  REVIEW_PROMPT,review_payload)
                    record.setdefault('review_attempts',[]).append(review); save()
                    try:
                        passed = validate_review(review,directions,plan['retained_ids'])
                        break
                    except ValueError as exc:
                        if review_attempt: raise
                        review_payload = {**review_payload,'format_correction':str(exc),
                                          'required_item_ids':[i['item_id'] for i in frame.outputs+frame.modifiers]}
                record['review']=review; save()
                if passed and not record['protection_issues']:
                    row.update(**rewritten,status='review_passed',accepted_mode='whole_fallback')
                    save()
            cache[key] = {k:row[k] for k in ('rewritten_request','status','directions','direction_revisions','active_hidden_fragments','effective_retention_ratio','attempts')}
            for optional in ('rewrite_route','accepted_mode','replacement_attempts'):
                if optional in row: cache[key][optional] = row[optional]
            save()
        trace['status']='review_passed' if all(r['status'] in ('review_passed','source_identity') for r in trace['levels']) else 'needs_review'
    except Exception as exc:
        trace.update(status='error',error=str(exc).replace(os.environ['OPENAI_API_KEY'],'<redacted>'))
    save()
    log = getattr(args, 'progress_log', None)
    message = f"{case['question_id']} {case['db_id']} {trace['status']}"
    if log:
        log(message)
    else:
        print(message, flush=True)
    return trace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='gpt-5.1')
    parser.add_argument('--review-model', help='Optional independent model for final semantic review')
    parser.add_argument('--rewrite-mode', choices=('local','whole'), default='local')
    parser.add_argument('--fraction', type=float, default=0.5)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--replay-frames', type=Path, help='Reuse audited stage-one outputs only')
    parser.add_argument('--ratios', help='Retained-fragment ratios, e.g. 0,0.3333333333333333,0.6666666666666666,1')
    parser.add_argument('--per-db', type=int, default=1)
    parser.add_argument('--offset', type=int, default=0, help='Question offset within each database')
    parser.add_argument('--repair-attempts', type=int, default=1)
    parser.add_argument('--question-ids', help='Explicit source question IDs, comma-separated')
    parser.add_argument('--selected-ids', help='Diagnostic override: fixed comma-separated frame IDs, incompatible with --ratios')
    args = parser.parse_args()
    fixed_ids = None
    if args.selected_ids:
        fixed_ids = [s.strip() for s in args.selected_ids.split(',')]
        if args.ratios or any(not s for s in fixed_ids) or len(set(fixed_ids)) != len(fixed_ids):
            parser.error('--selected-ids requires unique nonempty IDs and cannot be combined with --ratios')
    if not 0 < args.fraction <= 1: parser.error('fraction must be in (0,1]')
    if args.per_db < 1 or args.offset < 0 or not 0 <= args.repair_attempts <= 2:
        parser.error('positive per-db, nonnegative offset, and 0..2 repair attempts required')
    ratios = None
    if args.ratios:
        try:
            ratios = [float(r) for r in args.ratios.split(',')]
            if not ratios or ratios != sorted(ratios) or any(not math.isfinite(r) or not 0 <= r <= 1 for r in ratios):
                raise ValueError()
        except ValueError:
            parser.error('invalid retention ratios')
    load_dotenv(ROOT/'clause_frame_experiment/.env')
    official = json.loads((ROOT/'artifacts/bird300_question_sql_audit_20261009/official_dev_20251106.json').read_text(encoding='utf8'))
    schemas = json.loads((ROOT/'artifacts/bird_mini_dev_quality_audit_20261009/schemas.json').read_text(encoding='utf8'))
    cases = []
    for db in ('financial','california_schools','toxicology','european_football_2'):
        sources = [r for r in official if r['db_id']==db][args.offset:args.offset+args.per_db]
        for source in sources:
            cases.append({k:source[k] for k in ('question_id','db_id','question')})
    if args.question_ids:
        try:
            requested = [int(value) for value in args.question_ids.split(',')]
        except ValueError:
            parser.error('--question-ids requires integer IDs')
        indexed = {r['question_id']:r for r in official}
        if len(set(requested)) != len(requested) or any(i not in indexed for i in requested):
            parser.error('duplicate or unavailable question ID')
        cases = [{k:indexed[i][k] for k in ('question_id','db_id','question')} for i in requested]
    out = ROOT/'artifacts/flat_disclosure_smoke'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out.mkdir(parents=True)
    (out/'prompts.json').write_text(json.dumps({'frame':DISCLOSURE_FRAME_PROMPT,'role':ROLE_PROMPT,'inventory':INVENTORY_PROMPT,'obligation':OBLIGATION_PROMPT,'direction':DIRECTION_PROMPT,'direction_repair':DIRECTION_REPAIR_PROMPT,'rewrite':REWRITE_PROMPT,'replacement':REPLACEMENT_PROMPT,'review':REVIEW_PROMPT},indent=2),encoding='utf8')

    def run(case):
        return run_case(case, args, schemas, out, ratios, fixed_ids)
    with ThreadPoolExecutor(max_workers=2) as pool: traces=list(pool.map(run,cases))
    summary={'model':args.model,'review_model':args.review_model or args.model,'cases':len(traces),'review_passed':sum(t['status']=='review_passed' for t in traces),'needs_review':sum(t['status']=='needs_review' for t in traces),'errors':sum(t['status']=='error' for t in traces),'calls':sum(len(t['calls']) for t in traces),'tokens':sum((c.get('response',{}).get('usage') or {}).get('total_tokens',0) for t in traces for c in t['calls']),'note':'Model review is supporting evidence, not semantic proof. Ratios count fragments, not information bits.'}
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    print(out,flush=True)


if __name__=='__main__': main()
