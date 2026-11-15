"""Combine independent scope-aligned labels and audited reference witnesses.

No candidate SQL, gate, method identity, or candidate score is an input.
"""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'artifacts/main_analysis_server_20261102/requirement_retention'
def read(path):return [json.loads(s) for s in path.read_text(encoding='utf8').splitlines() if s.strip()]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path,rows):path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf8')

def unauthorized(reads,allowed,dataset):
    normalize=(lambda x:x) if dataset=='livesqlbench' else str.casefold
    grants={normalize(v) for v in allowed}
    return [(t,c) for t,c in reads if not (
        normalize(t+'.'+c) in grants or normalize(t+'.*') in grants if c else
        any(v.startswith(normalize(t)+'.') for v in grants))]

def main():
    p=argparse.ArgumentParser();p.add_argument('--alignment',type=Path,required=True)
    p.add_argument('--witnesses',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    a.alignment=a.alignment.resolve();a.witnesses=a.witnesses.resolve();a.out=a.out.resolve()
    if a.out.exists():raise ValueError('Use a new immutable output directory')
    requests={r['id']:r for r in read(BASE/'semantic_v1/blind_request_reference.jsonl')}
    audit=a.witnesses/'sql_witness_audit'
    job=json.loads((audit/'POSTGRES_JOB.json').read_text(encoding='utf8'))
    if not job['done'] or job['exitcode']!='0' or not job['downloaded']:raise ValueError('PostgreSQL audit unfinished')
    tasks={r['witness_id']:r for r in read(a.witnesses/'reference_witness_tasks.jsonl')}
    results=read(audit/'sqlite_witness_results.jsonl')+read(audit/'postgres_witness_results.jsonl')
    if len(results)!=len(tasks) or len({r['witness_id'] for r in results})!=len(results):
        raise ValueError('Incomplete or duplicate witness observations')
    for row in results:
        task=tasks[row['witness_id']]
        if any(row[k]!=task[k] for k in ('dataset','db_id','sql')):raise ValueError('Witness task/result drift')
    by_witness={r['witness_id']:r for r in results}
    links={(tuple(r['cluster_key']),r['goal_id']):r for r in read(audit/'goal_witness_links.jsonl')}
    output=[]
    for prior in read(a.alignment/'request_goal_feasibility_alignment.jsonl'):
        request=requests[prior['request_id']];row=dict(prior)
        link=links.get((tuple(prior['cluster_key']),prior['goal_id']))
        if link:
            observed=by_witness[link['witness_id']]
            check=dict(witness_id=link['witness_id'],status=observed['status'],
                projection_proof=link['projection_proof'],goal_sha256=link['goal_sha256'])
            if observed['status']=='executed_not_semantically_adjudicated':
                bad=unauthorized(observed['reads'],request['allowed_columns'],request['dataset'])
                check.update(reads=observed['reads'],unauthorized_dependencies=bad,
                    status='this_reference_implementation_unauthorized' if bad else 'authorized_and_executed')
                if not bad:
                    if prior['feasibility'] is False:
                        row.update(feasibility=None,status='conflicting_independent_evidence')
                    else:row.update(feasibility=True,status='authorized_original_Gold_goal_witness')
            else:check.update(error=observed.get('error'))
            row['current_original_Gold_witness']=check
        output.append(row)
    groups=defaultdict(list)
    for row in output:groups[row['request_id']].append(row)
    classes=[]
    for rid,rows in groups.items():
        positive=sum(r['feasibility'] is True for r in rows)
        uncertain=sum(r['feasibility'] is None for r in rows)
        status='has_confirmed_serviceable_goal' if positive else 'all_goals_annotated_unserviceable' if not uncertain else 'serviceability_unresolved'
        classes.append(dict(request_id=rid,original_permission=requests[rid]['original_permission'],
            status=status,serviceable_goals=positive,unknown_goals=uncertain,goals=len(rows),
            serviceable_goal_denominator_complete=uncertain==0,
            negative_labels_are_model_assisted=True,human_review_completed=False))
    a.out.mkdir(parents=True)
    write(a.out/'request_goal_serviceability.jsonl',output)
    write(a.out/'request_serviceability_classes.jsonl',classes)
    write(a.out/'pending_serviceability_annotation.jsonl',[r for r in output if r['feasibility'] is None])
    manifest=dict(status='reference_serviceability_evidence_not_final_conditional_retention',
        requests=len(requests),request_goal_pairs=len(output),witnesses=len(results),
        witness_statuses=dict(Counter(r['status'] for r in results)),
        goal_statuses=dict(Counter(r['status'] for r in output)),
        denied_request_classes=dict(Counter(r['status'] for r in classes if r['original_permission']=='denied')),
        denied_goal_denominators_complete=sum(r['original_permission']=='denied' and r['serviceable_goal_denominator_complete'] for r in classes),
        pending_denied_goal_pairs=sum(r['original_permission']=='denied' and r['feasibility'] is None for r in output),
        candidate_outputs_read=False,method_identity_read=False,human_review_completed=False,
        input_sha256={str(path.relative_to(ROOT)):sha(path) for path in [
            a.alignment/'request_goal_feasibility_alignment.jsonl',audit/'goal_witness_links.jsonl',
            audit/'sqlite_witness_results.jsonl',audit/'postgres_witness_results.jsonl']},
        script_sha256=sha(Path(__file__)))
    (a.out/'MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    print(json.dumps(manifest,indent=2))

if __name__=='__main__':main()
