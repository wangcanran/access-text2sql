"""Small auditable experiment using original BIRD questions, never gold SQL."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from clause_frame_experiment.parser import build_frame_prompt
from clause_frame_experiment.prompts import FRAME_SYSTEM_PROMPT, RELATION_SYSTEM_PROMPT
from clause_frame_experiment.model import parse_query_frame
from clause_frame_experiment.relations import validate_relations
from intent_refinement_bird_v2.llm_client import load_dotenv
from .prompts import (TARGET_PROMPT, MASK_PROMPT, FLUENCY_PROMPT, REVIEW_PROMPT,
                      ITEM_MASK_PROMPT, ITEM_PARTITION_PROMPT, FINE_ITEM_PARTITION_PROMPT)
from .database_context import build_database_context, with_database_context, DEFAULT_DB_ROOT

ROOT = Path(__file__).resolve().parents[1]

def load_replay_case(path):
    """Project only frozen public inputs, never earlier calls or hidden SQL labels."""
    prior=json.loads(Path(path).read_text(encoding='utf8'))
    case={k:prior[k] for k in ('question_id','db_id','question')}
    frame=parse_query_frame(prior['frame'])
    if frame.original_text!=case['question']: raise ValueError('replay original changed')
    relations=validate_relations(frame,prior['relations'])
    from .disclosure import build_inventory
    build_inventory(frame,prior['partition'])
    case['replay']={'frame':prior['frame'],'relations':relations,'partition':prior['partition'],
                    'source':str(Path(path).resolve())}
    return case

def polish_variant(call, variant):
    # Separate requests: even a less-hidden sibling could disclose this version's answers.
    raw=call('fluency',FLUENCY_PROMPT,{'natural_language':variant['natural_language']})
    if not isinstance(raw,dict) or set(raw)!={'natural_language'} or not isinstance(raw['natural_language'],str) or not raw['natural_language'].strip():
        raise ValueError('invalid fluency response')
    return {**variant,'natural_language':raw['natural_language']}

def render_planned_level(call, frame, relations, inventory, level, database_context=None):
    """One generation call; validation checks format, not semantic faithfulness."""
    from .disclosure import render_level_prompt_payload
    payload = render_level_prompt_payload(frame, relations, inventory, level)
    if not payload['hidden_item_ids']:
        return {'natural_language': frame.original_text}
    raw = call('hiding_level_' + str(level['level']), with_database_context(ITEM_MASK_PROMPT,database_context), payload)
    if (not isinstance(raw, dict) or set(raw) != {'natural_language'} or
            not isinstance(raw['natural_language'], str) or not raw['natural_language'].strip()):
        raise ValueError('expected only a nonempty natural_language string')
    return raw


def render_planned_levels(call, frame, relations, inventory, budget, database_context=None):
    """Yield audited levels; identical semantic plans share one generated text."""
    cache = {}
    for level in budget['levels']:
        row = dict(level)
        if level['status'] == 'ok':
            key = frozenset(level['hidden_item_ids'])
            if key in cache:
                source, text = cache[key]
                row.update(natural_language=text, reused_from_level=source)
            else:
                row.update(render_planned_level(call, frame, relations, inventory, level,database_context))
                cache[key] = (level['level'], row['natural_language'])
        yield row


def planned_generation_status(rows):
    return 'completed' if rows and all('natural_language' in r for r in rows) else 'no_disclosable_items'


def validate_targets(original, frame, plan):
    if not isinstance(plan,dict) or set(plan)!={'steps'} or not isinstance(plan['steps'],list) or not 1<=len(plan['steps'])<=3:
        raise ValueError('expected 1..3 target steps')
    items={x['item_id']:x['span'] for x in frame.outputs+frame.modifiers}
    seen=set(); ranges=[]
    for step in plan['steps']:
        if set(step)!={'targets','relation_reason'} or not isinstance(step['targets'],list) or not step['targets'] or not isinstance(step['relation_reason'],str):
            raise ValueError('invalid target step')
        for t in step['targets']:
            if set(t)!={'target_id','span','item_ids','hidden_information','original_answer'}:
                raise ValueError('invalid target fields')
            if any(not isinstance(t[k],str) or not t[k].strip() for k in ('target_id','span','hidden_information','original_answer')):
                raise ValueError('empty target information')
            if t['target_id'] in seen: raise ValueError('duplicate target id')
            seen.add(t['target_id'])
            span=t['span']
            if original.count(span)!=1 or span==original or t['original_answer'] not in span:
                raise ValueError('target or answer not grounded in original')
            if not isinstance(t['item_ids'],list) or not t['item_ids'] or any(i not in items or span not in items[i] for i in t['item_ids']):
                raise ValueError('target not in cited fragment')
            start=original.index(span); end=start+len(span)
            if any(start<e and s<end for s,e in ranges): raise ValueError('overlapping targets')
            ranges.append((start,end))
    return plan

def bind_replacements(targets, rendered):
    expected={t['target_id'] for s in targets['steps'] for t in s['targets']}
    if not isinstance(rendered,dict) or set(rendered)!={'replacements'} or not isinstance(rendered['replacements'],list):
        raise ValueError('expected replacements')
    by_id={}
    for r in rendered['replacements']:
        if not isinstance(r,dict) or set(r)!={'target_id','replacement'} or not isinstance(r['target_id'],str):
            raise ValueError('invalid replacement fields')
        if r['target_id'] not in expected or r['target_id'] in by_id or not isinstance(r['replacement'],str) or not r['replacement'].strip():
            raise ValueError('invalid replacement binding')
        by_id[r['target_id']]=r['replacement']
    if set(by_id)!=expected: raise ValueError('missing replacement')
    return {'steps':[{'relation_reason':s['relation_reason'],'edits':[
        {'span':t['span'],'item_ids':t['item_ids'],'hidden_information':t['hidden_information'],
         'replacement':by_id[t['target_id']]} for t in s['targets']]} for s in targets['steps']]}

def materialize(original, frame, plan):
    if set(plan) != {'steps'} or not isinstance(plan['steps'],list) or not 1 <= len(plan['steps']) <= 3:
        raise ValueError('expected 1..3 hiding steps')
    items={x['item_id']:x['span'] for x in frame.outputs+frame.modifiers}
    edits=[]; variants=[]
    for index,step in enumerate(plan['steps'],1):
        if set(step)!={'edits','relation_reason'} or not step['edits'] or not isinstance(step['relation_reason'],str):
            raise ValueError('invalid step')
        for edit in step['edits']:
            if set(edit)!={'span','replacement','hidden_information','item_ids'}:
                raise ValueError('invalid edit keys')
            span=edit['span']; replacement=edit['replacement']
            if not isinstance(span,str) or not span or original.count(span)!=1 or span==original:
                raise ValueError('edit must target unique proper substring')
            if not isinstance(replacement,str) or not replacement.strip() or span==replacement:
                raise ValueError('replacement must be nonempty and changed')
            if not isinstance(edit['hidden_information'],str) or not edit['hidden_information'].strip():
                raise ValueError('missing hidden information')
            if not isinstance(edit['item_ids'],list) or not edit['item_ids'] or any(i not in items or span not in items[i] for i in edit['item_ids']):
                raise ValueError('edit is not grounded in cited fragments')
            start=original.index(span); end=start+len(span)
            if any(start<e and s<end for s,e,_ in edits): raise ValueError('overlapping cumulative edits')
            edits.append((start,end,replacement))
        text=original
        for start,end,replacement in sorted(edits,reverse=True): text=text[:start]+replacement+text[end:]
        variants.append({'step':index,'natural_language':text,'cumulative_edit_count':len(edits)})
    if len({original,*[v['natural_language'] for v in variants]})!=len(variants)+1:
        raise ValueError('duplicate variants')
    return variants

def schema_context(raw):
    tables=[]; fks=[]; meanings={}
    table_names = {name.casefold(): name for name in raw['tables']}
    for name,t in raw['tables'].items():
        tables.append({'name':name,'columns':[{'name':c['name'],'type':c['type']} for c in t['columns']]})
        for fk in t['foreign_keys']:
            target = table_names.get(fk['table'].casefold(), fk['table'])
            target_columns = raw['tables'].get(target, {}).get('columns', [])
            target_column = fk['to']
            if target_column is None:
                # SQLite REFERENCES parent without a column list names the
                # parent's ordered primary key, not an unknown target column.
                primary = sorted((c for c in target_columns if c.get('pk', 0)), key=lambda c: c['pk'])
                parts = [p for p in t['foreign_keys'] if p['id'] == fk['id']]
                if len(primary) != len(parts) or not 0 <= fk['seq'] < len(primary):
                    raise ValueError(f'Cannot resolve implicit foreign key {name} -> {target}')
                target_column = primary[fk['seq']]['name']
            else:
                target_column = next((c['name'] for c in target_columns
                                      if c['name'].casefold() == target_column.casefold()), target_column)
            fks.append({'from_table':name,'from_column':fk['from'],'to_table':target,
                        'to_column':target_column,'constraint_id':fk['id'],'sequence':fk['seq'],'source':'sqlite_pragma'})
        for d in raw['descriptions'].get(name,{}).get('rows',[]):
            meanings[name+'.'+d['original_column_name']]=' | '.join(str(d[k]) for k in
                ('column_name','column_description','value_description','') if d.get(k))
    return {'tables':tables,'foreign_keys':fks,'column_language':meanings}

def main():
    from openai import OpenAI
    p=argparse.ArgumentParser(); p.add_argument('--limit',type=int,default=4)
    mode=p.add_mutually_exclusive_group()
    mode.add_argument('--percentage',dest='percentage',action='store_true',default=True,
                      help='Use graph-ordered budgets and full-text generation (default)')
    mode.add_argument('--legacy',dest='percentage',action='store_false',
                      help='Explicitly run the historical span replacement and fluency pipeline')
    p.add_argument('--refine-items',action='store_true',help='Partition items into disjoint leaf blocks (requires --percentage)')
    p.add_argument('--fine-partition',action='store_true',
                   help='Use the experimental independent-choice fine partition prompt')
    p.add_argument('--database-context',action='store_true',help='Add fresh BIRD CSV descriptions and schema to full-text generation only')
    p.add_argument('--bird-description-root',type=Path,default=DEFAULT_DB_ROOT)
    p.add_argument('--replay-audit',type=Path,help='Reuse validated frame, relations and partition from an earlier audit directory')
    p.add_argument('--ratios',default='0,0.3333333333333333,0.6666666666666666,1')
    args=p.parse_args()
    if args.limit < 1: p.error('--limit must be positive')
    import math
    try:
        ratios=[float(r) for r in args.ratios.split(',')]
        if not ratios or any(not math.isfinite(r) or not 0 <= r <= 1 for r in ratios) or ratios != sorted(ratios):
            raise ValueError()
    except ValueError:
        p.error('--ratios must be finite, nondecreasing values in [0,1]')
    if args.refine_items and not args.percentage:
        p.error('--refine-items requires --percentage')
    if args.fine_partition and not args.refine_items:
        p.error('--fine-partition requires --refine-items')
    if args.fine_partition and args.replay_audit:
        p.error('--fine-partition must run a new partition; replay reuses the old partition')
    if args.replay_audit and not (args.percentage and args.refine_items):
        p.error('--replay-audit requires --percentage --refine-items')
    load_dotenv(ROOT/'clause_frame_experiment/.env')
    model=os.getenv('OPENAI_FRAME_MODEL') or os.getenv('OPENAI_MODEL','gpt-4o-mini')
    if args.replay_audit:
        paths=sorted(args.replay_audit.glob('[0-9]*.json'))
        if not paths: p.error('no case JSON files found in replay audit')
        cases=[load_replay_case(path) for path in paths[:args.limit]]
        schemas={}
    else:
        official=json.loads((ROOT/'artifacts/bird300_question_sql_audit_20261009/official_dev_20251106.json').read_text(encoding='utf8'))
        schemas=json.loads((ROOT/'artifacts/bird_mini_dev_quality_audit_20261009/schemas.json').read_text(encoding='utf8'))
        # Only question and source identifiers leave the original annotation loader.
        cases=[]
        for db in ('financial','california_schools','toxicology','european_football_2'):
            r=next(r for r in official if r['db_id']==db)
            cases.append({k:r[k] for k in ('question_id','db_id','question')})
    out=ROOT/'artifacts/request_disclosure_smoke'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    if args.database_context and not schemas:
        schemas=json.loads((ROOT/'artifacts/bird_mini_dev_quality_audit_20261009/schemas.json').read_text(encoding='utf8'))
    out.mkdir(parents=True)
    partition_prompt = FINE_ITEM_PARTITION_PROMPT if args.fine_partition else ITEM_PARTITION_PROMPT
    (out/'prompt_snapshot.json').write_text(json.dumps({'frame':FRAME_SYSTEM_PROMPT,'relations':RELATION_SYSTEM_PROMPT,
        'targets':TARGET_PROMPT,'item_partition':partition_prompt,'item_mask':ITEM_MASK_PROMPT,'mask':MASK_PROMPT,'fluency':FLUENCY_PROMPT,'review':REVIEW_PROMPT},ensure_ascii=False,indent=2),encoding='utf8')
    def worker(case):
        trace={k:case[k] for k in ('question_id','db_id','question')}
        trace.update(calls=[],sql_or_evidence_sent=False)
        replay=case.get('replay')
        if replay: trace['replay_source']=replay['source']
        path=out/(str(case['question_id'])+'.json')
        def save(): path.write_text(json.dumps(trace,ensure_ascii=False,indent=2),encoding='utf8')
        client=OpenAI(api_key=os.environ['OPENAI_API_KEY'],base_url=os.getenv('OPENAI_BASE_URL'),timeout=120,max_retries=1)
        def call(stage,prompt,payload=None):
            messages=[{'role':'system','content':prompt}]
            if payload is not None: messages.append({'role':'user','content':json.dumps(payload,ensure_ascii=False)})
            record={'stage':stage,'request':{'model':model,'temperature':0,'response_format':{'type':'json_object'},'messages':messages}}
            trace['calls'].append(record); save()
            response=client.chat.completions.create(**record['request']); record['response']=response.model_dump(mode='json'); save()
            return json.loads(response.choices[0].message.content)
        try:
            database_context=None
            if args.database_context:
                database_context,provenance=build_database_context(case['db_id'],schemas[case['db_id']],args.bird_description_root)
                trace['database_context']=database_context; trace['database_context_provenance']=provenance; save()
            raw=replay['frame'] if replay else call('segmentation',build_frame_prompt(case['question'],schema_context=schema_context(schemas[case['db_id']]),db_name=case['db_id']))
            frame=parse_query_frame(raw)
            if frame.original_text!=case['question']: raise ValueError('original changed')
            trace['frame']=raw
            relations=validate_relations(frame,replay['relations'] if replay else call('relations',RELATION_SYSTEM_PROMPT.replace('[[frame]]',json.dumps(raw,ensure_ascii=False))))
            trace['relations']=relations
            payload={'frame':raw,'relations':relations}
            if args.percentage:
                from .disclosure import build_inventory, plan_disclosure
                inventory=build_inventory(frame)
                if args.refine_items:
                    partition=replay['partition'] if replay else call('item_partition',partition_prompt,payload)
                    trace['partition']=partition; save()
                    inventory=build_inventory(frame,partition)
                trace['inventory']=inventory
                trace['partition_mode']='fine_grained' if args.fine_partition else 'conservative'
                trace['disclosure_contract']='planned_full_text_v1'
                trace['inventory_contract']='disjoint_leaf_items_v1' if args.refine_items else 'stage_one_items_v1'
                budget=plan_disclosure(frame,relations,inventory,ratios)
                trace['disclosure_plan']=budget; trace['levels']=[]; save()
                for row in render_planned_levels(call,frame,relations,inventory,budget,database_context):
                    trace['levels'].append(row)
                    save()
                trace['distinct_level_texts']=len({' '.join(r['natural_language'].split()).casefold()
                    for r in trace['levels'] if 'natural_language' in r})
                trace['status']=planned_generation_status(trace['levels']); save()
                print(json.dumps({'question_id':case['question_id'],'status':trace['status'],'path':str(path)}),flush=True)
                return trace
            target_raw=call('hiding_targets',TARGET_PROMPT,payload); trace['targets']=target_raw
            targets=validate_targets(case['question'],frame,target_raw)
            rendered=call('hiding',MASK_PROMPT,{**payload,'targets':targets}); trace['replacements']=rendered
            plan=bind_replacements(targets,rendered); trace['plan']=plan
            drafts=materialize(case['question'],frame,plan); trace['draft_variants']=drafts
            variants=[]; trace['variants']=variants
            for draft in drafts:
                variants.append(polish_variant(call,draft)); save()
            if len({' '.join(v['natural_language'].split()).casefold() for v in variants})!=len(variants):
                raise ValueError('fluency rewrite collapsed distinct variants')
            review=call('review',REVIEW_PROMPT,{**payload,'plan':plan,'variants':variants})
            rs=review['reviews']
            if len(rs)!=len(variants) or {r['step'] for r in rs}!={v['step'] for v in variants}: raise ValueError('review coverage')
            for r in rs:
                if r['verdict'] not in ('pass','uncertain','fail') or not isinstance(r['issues'],list) or (r['verdict']=='pass')!= (not r['issues']): raise ValueError('review contract')
            trace['review']=review; trace['status']='completed'
        except Exception as exc:
            trace['status']='error'; trace['error']=str(exc).replace(os.environ['OPENAI_API_KEY'],'<redacted>')
        save(); print(json.dumps({'question_id':case['question_id'],'status':trace['status'],'path':str(path)}),flush=True)
        return trace
    with ThreadPoolExecutor(max_workers=2) as pool: traces=list(pool.map(worker,cases[:args.limit]))
    summary={'model':model,'percentage_mode':args.percentage,'cases':len(traces),'completed':sum(t['status']=='completed' for t in traces),
             'replay_audit':str(args.replay_audit.resolve()) if args.replay_audit else None,
             'review_verdicts':[r['verdict'] for t in traces for r in t.get('review',{}).get('reviews',[])],
             'note':'Structural checks and same-model reviews are not semantic proof. No disclosure ratios inferred from edge counts.'}
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    print(str(out),flush=True)

if __name__=='__main__': main()
