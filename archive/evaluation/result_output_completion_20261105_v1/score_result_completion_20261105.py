"""Read-only native scoring of fixed legal Gold outputs; no model inference.

Executed in a separate server job directory with frozen input and helper modules.
Output mapping is selected by a declared injective result search, independent
of expression spelling. No historical score overrides this fresh comparison.
"""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from retention_sql_output_units import output_contract, conservative_signatures, bind_positions, parse_readonly, sha
from retention_result_matcher import compare_goal_subset
from result_completion_matcher import match_results


def load(path):
    return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]


def jsonwrite(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=True, default=str))
    temp.replace(path)


def cluster(task, dbroot, seconds, row_cap):
    ds, db, _ = task['cluster_key']
    dialect = 'postgres' if ds == 'livesqlbench' else 'sqlite'
    result = dict(cluster_key=task['cluster_key'], records=[], status='running')
    con = None
    try:
        if dialect == 'postgres':
            import psycopg2
            con = psycopg2.connect(host='/var/run/postgresql', user='root', dbname=db,
                                  connect_timeout=10,
                                  options=f'-c default_transaction_read_only=on -c statement_timeout={int(seconds*1000)}')
            con.set_session(readonly=True, autocommit=False, isolation_level='REPEATABLE READ')
            with con.cursor() as cur:
                cur.execute("SELECT table_name,column_name,data_type FROM information_schema.columns WHERE table_schema='public' ORDER BY ordinal_position")
                schema = {}
                for table, col, typ in cur.fetchall():
                    schema.setdefault(table, {})[col] = typ
        else:
            path = Path(dbroot)/ds/db/(db+'.sqlite')
            con = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, cached_statements=0)
            con.text_factory = lambda b: b.decode('utf8', errors='surrogateescape')
            con.execute('PRAGMA query_only=ON')
            schema = {n: {r[1]: r[2] or 'TEXT' for r in con.execute('PRAGMA table_info("'+n.replace('"','""')+'")')}
                      for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
            con.execute('BEGIN')

        def execute(sql):
            parse_readonly(sql, dialect)
            if dialect == 'sqlite':
                deadline = time.monotonic()+seconds
                con.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            cur = con.cursor()
            if dialect == 'postgres':
                cur.execute('SAVEPOINT completion_query')
            try:
                cur.execute(sql)
                rows = cur.fetchmany(row_cap+1)
                if len(rows) > row_cap:
                    raise RuntimeError('row_cap_incomplete_result')
                return rows, len(cur.description)
            except BaseException:
                if dialect == 'postgres':
                    cur.execute('ROLLBACK TO SAVEPOINT completion_query')
                raise
            finally:
                if dialect == 'postgres':
                    cur.execute('RELEASE SAVEPOINT completion_query')
                cur.close()

        gold, width = execute(task['gold_sql'])
        result.update(gold_rows=len(gold), gold_width=width, gold_result_sha256=sha(gold))
        if task.get('expected_gold_sha256'):
            assert result['gold_result_sha256'] == task['expected_gold_sha256'], 'Gold snapshot changed'
        cache = {}
        for item in task['candidates']:
            item=dict(item)
            if item['sql_access']=='unresolved' and dialect=='sqlite' and item.get('allowed_columns'):
                from recheck_retention_dependency_audit import dependencies,access
                try:
                    reads=dependencies(path,item['sql'])
                    status,bad=access(reads,item['allowed_columns'])
                    item.update(sql_access=status,access_recheck=dict(reads=reads,unauthorized_dependencies=bad,
                                version='native_sqlite_explain_v2'))
                except Exception as exc:
                    item['access_recheck_error']=type(exc).__name__+': '+str(exc)
            r = dict(item)
            legal = item['legal_gold_columns']
            if legal == 'all':
                legal = list(range(width))
            r['legal_gold_columns'] = legal
            assert legal and all(0 <= i < width for i in legal)
            sql = item['sql']
            if item['sql_access'] == 'no_sql' or not sql.strip():
                r.update(status='scored', score_lower=0., score_upper=0., reason='no_sql')
            elif item['sql_access'] == 'unauthorized':
                r.update(status='scored', score_lower=0., score_upper=0., reason='unauthorized')
            else:
                if sql not in cache:
                    entry = {}
                    try:
                        data, cw = execute(sql)
                        entry.update(data=data, candidate_width=cw, result_sha256=sha(data))
                    except Exception as exc:
                        message = type(exc).__name__+': '+str(exc)
                        temporary = any(s in message.lower() for s in (
                            'timeout', 'timed out', 'interrupted', 'canceling statement', 'row_cap',
                            'connection', 'server closed', 'could not connect'))
                        entry.update(execution_error=message, execution_pending=temporary)
                    cache[sql] = entry
                entry = cache[sql]
                if 'execution_error' in entry:
                    pending = entry['execution_pending']
                    r.update(status='execution_pending' if pending else 'scored', score_lower=0.,
                             score_upper=1. if pending else 0., execution_error=entry['execution_error'])
                elif 'binding_error' in entry:
                    r.update(status='mapping_unresolved', score_lower=0.,
                             score_upper=min(entry['candidate_width'],len(legal))/len(legal),
                             binding_error=entry['binding_error'])
                else:
                    r.update(match_results(gold,entry['data'],legal,entry['candidate_width']))
                    r['candidate_result_sha256'] = entry['result_sha256']
                if item['sql_access'] == 'unresolved' and r['score_upper'] > 0:
                    r.update(conditional_on_access_lower=r['score_lower'], score_lower=0.,
                             status='permission_unresolved')
            result['records'].append(r)
        result['status'] = 'complete'
    except Exception as exc:
        result.update(status='reference_unresolved', error=type(exc).__name__+': '+str(exc))
        done = {r['candidate_id'] for r in result['records']}
        for item in task['candidates']:
            if item['candidate_id'] not in done:
                result['records'].append(dict(item, status='reference_unresolved', score_lower=0., score_upper=1.,
                                              reference_error=result['error']))
    finally:
        if con is not None:
            con.close()
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--job', type=Path, required=True)
    p.add_argument('--db-root', default='/path/to/access/data/manual_flat_gold_v1_databases_20261031/db_assets')
    p.add_argument('--workers', type=int, default=6)
    p.add_argument('--seconds', type=float, default=15)
    p.add_argument('--row-cap', type=int, default=500000)
    args = p.parse_args()
    tasks = load(args.job/'tasks.jsonl')
    dest = args.job/'clusters'
    dest.mkdir(exist_ok=True)
    pending = [t for t in tasks if not (dest/(sha(t['cluster_key'])[:24]+'.json')).exists()]
    done = len(tasks)-len(pending)
    def publish(state):
        jsonwrite(args.job/'STATUS.json', dict(state=state, completed_clusters=done, total_clusters=len(tasks)))
    publish('running')
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(cluster,t,args.db_root,args.seconds,args.row_cap): t for t in pending}
        for future in as_completed(futures):
            task = futures[future]
            result = future.result()
            jsonwrite(dest/(sha(task['cluster_key'])[:24]+'.json'),result)
            done += 1
            publish('running')
            print(json.dumps(dict(done=done,total=len(tasks),cluster=task['cluster_key'],status=result['status'])),flush=True)
    records = []
    for task in tasks:
        records.extend(json.loads((dest/(sha(task['cluster_key'])[:24]+'.json')).read_text())['records'])
    assert len(records) == len({r['candidate_id'] for r in records})
    (args.job/'scores.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=True,default=str)+'\n' for r in records))
    jsonwrite(args.job/'COMPLETE.json',dict(clusters=len(tasks),scores=len(records),statuses=dict(Counter(r['status'] for r in records))))
    publish('complete')


if __name__ == '__main__':
    main()
