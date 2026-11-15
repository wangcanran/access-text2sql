"""Native joint Gold retention on the same frozen 80 reference requests."""
import json,sys,time,fcntl
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
ROOT=Path('/path/to/access');JOB=ROOT/'output/strengthened_baseline_final_eval_20261104'
EXTRA=ROOT/'output/extra_qwen_paired_2800_20261104'
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(EXTRA))
def read(p):return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def write(p,rs):p.write_text(''.join(json.dumps(r,default=str,ensure_ascii=False)+'\n' for r in rs))
def main():
    out=JOB/'joint_retention';out.mkdir(exist_ok=True)
    with (out/'pipeline.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        status=out/'STATUS.json'
        def save(x):
            tmp=status.with_suffix('.tmp');tmp.write_text(json.dumps(x,indent=2));tmp.replace(status)
        save(dict(state='native_auditing',started_at=time.time()))
        import audit_retention_blind_candidates_remote as audit
        from run_retention_witness_audit_remote import load_scorer
        from recheck_retention_dependency_audit import dependencies
        from run_joint_gold_evaluation import execute_task
        from retention_output_bindings import output_signatures
        from run_retention_sql_output_units import schema_for
        audit.MOD=load_scorer(str(ROOT/'scripts/score_paired_main_sql_20261102_remote.py'))
        def deps(path,sql):
            try:return dependencies(path,sql),None
            except Exception as e:return None,str(e)
        audit.MOD.sqlite_dependencies=deps
        refs={r['request_id']:r for r in read(EXTRA/'retention_references.jsonl')};assert len(refs)==80
        write(out/'references.jsonl',list(refs.values()))
        identity={};native_tasks=[];zero=[]
        for arm in ['serial','joint']:
            front={r['id']:r for r in read(JOB/arm/'evaluation_rows.jsonl')};tasks={r['id']:r for r in read(JOB/arm/'sql_tasks.jsonl')};scores={r['id']:r for r in read(JOB/arm/'sql_results.jsonl')}
            for rid,ref in refs.items():
                cid=arm+':'+rid;common=dict(candidate_id=cid,request_id=rid,method=arm,model='deepseek_flash',cluster_key=ref['cluster_key']);identity[cid]=common
                s=scores.get(rid);t=tasks.get(rid)
                if not s or s['generation_status']!='sql':
                    assert not s or s['generation_status']=='downstream_refusal'
                    zero.append(dict(common,status='scored',joint_retention=0.,proof='explicit front/backend refusal; no SQL delivered'));continue
                native_tasks.append(dict(candidate_id=cid,request_id=rid,reference_snapshot_sha256=ref['expected_gold_sha256'],candidate_sql=t['sql'],dataset=t['dataset'],db_id=t['db_id'],allowed_columns=t['allowed_columns']))
        write(out/'native_gate_tasks.jsonl',native_tasks)
        with ThreadPoolExecutor(max_workers=4) as pool:gates=list(pool.map(audit.score,native_tasks))
        taskmap={t['candidate_id']:t for t in native_tasks}
        for g in gates:
            t=taskmap[g['candidate_id']]
            if g['gate_reason']=='sql_parser_or_readonly_guard_unresolved' and t['dataset']=='livesqlbench':
                try:
                    with audit.MOD.pg_connection(t['db_id']) as c:
                        with c.cursor() as cur:cur.execute('EXPLAIN '+t['candidate_sql'])
                except Exception as e:
                    g['native_prepare_error']=type(e).__name__+': '+str(e)
                    if type(e).__name__ in ('SyntaxError','UndefinedColumn','UndefinedTable','UndefinedFunction','DatatypeMismatch','AmbiguousColumn'):g.update(gate=0,gate_reason='native_prepare_failed')
        write(out/'native_gates.jsonl',gates)
        grouped={};mapping=[]
        for g in gates:
            cid=g['candidate_id'];rid=g['request_id'];ref=refs[rid];common=identity[cid];t=taskmap[cid]
            if g['gate']==0:
                zero.append(dict(common,status='scored',joint_retention=0.,proof=g['gate_reason']));continue
            ds,db,_=ref['cluster_key'];dialect='postgres' if ds=='livesqlbench' else 'sqlite';path=audit.MOD.DB/ds/db/(db+'.sqlite')
            review=None;error=None
            try:
                schema=audit.MOD.pg_schema(db) if dialect=='postgres' else schema_for(path)
                gs=output_signatures(ref['gold_sql'],schema,dialect);cs=output_signatures(t['candidate_sql'],schema,dialect)
                options=[[j for j,c in enumerate(cs) if c['signature'] is not None and c['signature']==gs[i]['signature']] for i in ref['authorized_gold_columns']]
                import sqlglot
                from sqlglot import exp
                tree=sqlglot.parse_one(t['candidate_sql'],read=dialect)
                if tuple(ref['cluster_key'])==('bird','codebase_community',649) and len(tree.expressions)==1 and isinstance(tree.expressions[0],exp.Count):options=[[0]];review='same prior frozen history COUNT output rule'
                if tuple(ref['cluster_key'])==('spider','student_transcripts_tracking',123) and len(tree.expressions)==1 and isinstance(tree.expressions[0],exp.Column) and tree.expressions[0].name.lower()=='course_id':options=[[0]];review='same prior declared Courses/Sections course_id key correspondence'
            except Exception as e:options=[[] for _ in ref['authorized_gold_columns']];error=str(e)
            mapping.append(dict(common,gold_columns=ref['authorized_gold_columns'],mapping_options=options,candidate_sql=t['candidate_sql'],gold_sql=ref['gold_sql'],binding_error=error,review_rule=review))
            ck=tuple(ref['cluster_key'])
            grouped.setdefault(ck,dict(cluster_key=ref['cluster_key'],gold_sql=ref['gold_sql'],expected_gold_sha256=ref['expected_gold_sha256'],path=str(path),candidates=[]))['candidates'].append(dict(candidate_id=cid,request_id=rid,sql=t['candidate_sql'],gold_columns=ref['authorized_gold_columns'],mapping_options=options))
        write(out/'mapping_audit.jsonl',mapping);write(out/'joint_tasks.jsonl',list(grouped.values()));save(dict(state='joint_comparing',gate_reasons=dict(Counter(g['gate_reason'] for g in gates))))
        with ThreadPoolExecutor(max_workers=4) as pool:native=list(pool.map(execute_task,grouped.values()))
        write(out/'joint_native_results.jsonl',native);gateidx={g['candidate_id']:g for g in gates};results=zero
        for r in native:
            if r['status']!='completed':
                for c in grouped[tuple(r['cluster_key'])]['candidates']:results.append(dict(identity[c['candidate_id']],status='reference_unresolved',joint_retention=None,error=r.get('error')))
                continue
            for c in r['candidates']:
                g=gateidx[c['candidate_id']];s=dict(identity[c['candidate_id']],**{k:v for k,v in c.items() if k not in identity[c['candidate_id']]})
                if c.get('joint_retention',0) is not None and c.get('joint_retention',0)>0 and g['gate']!=1:s.update(status='permission_unresolved',joint_lower=0.,joint_upper=c['joint_retention'],joint_retention=None)
                results.append(s)
        assert len(results)==len({s['candidate_id'] for s in results})==160
        write(out/'candidate_scores.jsonl',results)
        summary=[]
        for arm in ['serial','joint']:
            rs=[s for s in results if s['method']==arm];known=[s for s in rs if s['status']=='scored']
            summary.append(dict(method=arm,N=80,scored=len(known),unresolved=len(rs)-len(known),mean_joint_retention=sum(s['joint_retention'] for s in known)/80 if len(known)==80 else None,nonzero=sum(s['joint_retention']>0 for s in known)))
        save(dict(state='complete' if all(s['status']=='scored' for s in results) else 'needs_evidence_review',total=160,statuses=dict(Counter(s['status'] for s in results)),summary=summary));print(status.read_text(),flush=True)
if __name__=='__main__':main()
