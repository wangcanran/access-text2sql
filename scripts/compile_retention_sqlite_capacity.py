"""Recover reference permission dependencies with native read-only compilation.

EXPLAIN compiles the SELECT; it does not consume its result rows. Consequently
this audit does not conflate permission capacity with row caps or text decoding.
"""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sqlglot
from sqlglot import exp

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'artifacts/main_analysis_server_20261102/requirement_retention'
DB=ROOT/'data/manual_flat_gold_v1_databases_20261031/db_assets'


def compile_dependencies(sql,path):
    trees=sqlglot.parse(sql,read='sqlite')
    if len(trees)!=1 or not isinstance(trees[0],(exp.Select,exp.SetOperation)):
        raise ValueError('A single read-only SELECT is required')
    if any(isinstance(n,(exp.DML,exp.DDL,exp.Command,exp.Into)) for n in trees[0].walk()):
        raise ValueError('Non-read SQL')
    reads=set()
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,cached_statements=0)) as con:
        con.execute('PRAGMA query_only=ON')
        physical={r[0].casefold() for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        virtual={c.alias_or_name.casefold() for c in trees[0].find_all(exp.CTE)}
        def authorizer(action,table,column,database,source):
            if action==sqlite3.SQLITE_READ:
                if database=='main' or (database is None and not column and (table or '').casefold() in physical):
                    reads.add((table or '',column or ''));return sqlite3.SQLITE_OK
                if database is None and not column and (table or '').casefold() in virtual:
                    return sqlite3.SQLITE_OK
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_RECURSIVE) else sqlite3.SQLITE_DENY
        con.set_authorizer(authorizer)
        # Consumes VM opcode rows, never the query's data rows.
        con.execute('EXPLAIN '+sql).fetchall()
    return sorted(reads)


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    source=BASE/'serviceability_goal_witnesses_v25/sql_witness_audit/sqlite_witness_results.jsonl'
    if a.out.exists():raise ValueError('Use a new immutable output directory')
    pending=[json.loads(s) for s in source.read_text(encoding='utf8').splitlines() if s.strip()]
    pending=[r for r in pending if r['status']!='executed_not_semantically_adjudicated']
    results=[]
    for old in pending:
        task={k:old[k] for k in ('witness_id','dataset','db_id','sql')}
        path=DB/task['dataset']/task['db_id']/(task['db_id']+'.sqlite')
        try:
            reads=compile_dependencies(task['sql'],path)
            row=dict(**task,status='reference_dependencies_compiled',reads=reads,
                dependency_method='native_sqlite_EXPLAIN_authorizer',query_data_rows_executed=False)
        except Exception as e:
            row=dict(**task,status='reference_dependency_compile_unresolved',error_type=type(e).__name__,error=str(e))
        row['previous_execution_audit_status']=old['status'];results.append(row)
    a.out.mkdir(parents=True)
    (a.out/'dependency_results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in results),encoding='utf8')
    manifest=dict(tasks=len(results),statuses=dict(Counter(r['status'] for r in results)),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),candidate_outputs_read=False,
        model_feasibility_labels_read=False,query_data_rows_executed=False)
    (a.out/'MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
