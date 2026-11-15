"""Versioned audit corrections; preserve frozen evaluation files unchanged."""
import json
import sqlite3
import sqlglot
from sqlglot import exp
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'artifacts/main_analysis_server_20261102'
OUT=BASE/'requirement_retention/semantic_v1'
DB=ROOT/'data/manual_flat_gold_v1_databases_20261031/db_assets'

def jl(p): return [json.loads(s) for s in p.read_text(encoding='utf8').splitlines() if s.strip()]

def dependencies(path,sql):
    reads=set()
    trees=sqlglot.parse(sql,read='sqlite')
    if len(trees)!=1 or not isinstance(trees[0],(exp.Select,exp.SetOperation)):
        raise ValueError('single SELECT required')
    if any(isinstance(n,(exp.DML,exp.DDL,exp.Command,exp.Into)) for n in trees[0].walk()):
        raise ValueError('non-read statement')
    ctes={n.alias_or_name.casefold() for n in trees[0].find_all(exp.CTE)}
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,cached_statements=0) as c:
        c.execute('PRAGMA query_only=ON')
        tables={r[0].casefold() for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        def auth(action,table,col,db,source):
            if action==sqlite3.SQLITE_READ:
                if db is None and not col and (table or '').casefold() in ctes and (table or '').casefold() not in tables:
                    return sqlite3.SQLITE_OK
                if db!='main' and not (db is None and not col and (table or '').casefold() in tables):
                    return sqlite3.SQLITE_DENY
                reads.add((table or '',col or ''))
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_RECURSIVE) else sqlite3.SQLITE_DENY
        c.set_authorizer(auth)
        c.execute('EXPLAIN '+sql).fetchall()
    return sorted(reads)

def access(reads,allowed):
    grants={x.casefold() for x in allowed}
    bad=[]
    for t,col in reads:
        table=t.casefold()
        good=(f'{table}.{col.casefold()}' in grants or f'{table}.*' in grants) if col else any(x.startswith(table+'.') for x in grants)
        if not good: bad.append(f'{t}.{col or "<row-access>"}')
    return ('unauthorized' if bad else 'authorized'),bad

def main():
    tasks={(r['method'],r['model'],r['id']):r for r in jl(BASE/'paired_evaluation/all_sql_tasks.jsonl')}
    corrections=[]
    for r in jl(BASE/'paired_evaluation/sql_results.jsonl'):
        if r['dataset']=='livesqlbench' or r.get('dependency_error')!='not authorized': continue
        t=tasks[r['method'],r['model'],r['id']]
        p=DB/r['dataset']/r['db_id']/(r['db_id']+'.sqlite')
        v=dict(r,previous_sql_access=r['sql_access'],previous_dependency_error=r['dependency_error'],
               access_audit_version='sqlite_v2_null_namespace_row_reads')
        try:
            reads=dependencies(p,t['sql'])
            status,bad=access(reads,t['allowed_columns'])
            v.update(sql_access=status,unauthorized_dependencies=bad,reads=reads,dependency_error=None)
        except (sqlite3.Error,ValueError,sqlglot.errors.ParseError) as e:
            v.update(dependency_error=str(e))
        corrections.append(v)
    (OUT/'sql_dependency_corrections_v2.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in corrections),encoding='utf8')
    summary=dict(records_rechecked=len(corrections),status_counts=dict(Counter(r['sql_access'] for r in corrections)),
        by_model={m:dict(Counter(r['sql_access'] for r in corrections if r['model']==m)) for m in sorted({r['model'] for r in corrections})},
        frozen_source_modified=False,semantic_scores_computed=False)
    (OUT/'AUDIT_CORRECTION_SUMMARY.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()
