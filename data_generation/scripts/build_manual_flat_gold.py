"""Join manually certified flat cases with their RBAC role rows.

The output is an evaluation-ready gold set: one row per certified case, role,
and disclosure level, while preserving the original gold SQL and permission
label.  Matching is done against the original request and database id so the
manual queue remains independent of the RBAC file's internal identifiers.
"""
from __future__ import annotations

import argparse, hashlib, json, re
from collections import Counter
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / 'artifacts/flat_disclosure_cross/recoverable_flat200_fullpool_queue_20261031'

ROLE_FILES = {
    'bird': ROOT / 'data/selected/bird/column_level_rbac_dataset_bird_v3_no_sm.json',
    'spider': ROOT / 'data/selected/spider/column_level_rbac_dataset_spider_v3_no_sm.json',
    'livesql': ROOT / 'data/selected/livesqlbench-full/crud_rbac_dataset_v3_no_sm.gt.json',
}

def norm(s):
    s = str(s or '').replace('\ufffd', '').replace('�', '')
    return re.sub(r'\s+', ' ', s).strip().casefold()

def load_queue(dataset):
    p = QUEUE / f'{dataset}_full_pool_review_queue.jsonl'
    return [json.loads(x) for x in p.read_text(encoding='utf8').splitlines() if x.strip()]

def load_certified():
    d = json.loads((QUEUE / 'manual_certified.json').read_text(encoding='utf8'))
    return {(x['dataset'], str(x['question_id'])): x for x in d['certified']}

def row_signature(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, default=ROOT/'data/gold/manual_flat_gold_v1')
    args = ap.parse_args()
    certified = load_certified()
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = {'version':'manual_flat_gold_v1', 'datasets':{}, 'unmatched':{}, 'duplicate_matches':{}}
    all_levels = {0: [], 1: [], 2: []}
    all_original = []
    for dataset, role_path in ROLE_FILES.items():
        cases = {str(c['question_id']): c for c in load_queue(dataset)
                 if (dataset, str(c['question_id'])) in certified}
        raw_rows = json.loads(role_path.read_text(encoding='utf8'))
        indexes = {}
        for i, row in enumerate(raw_rows):
            q = row.get('input') or row.get('question')
            key = (norm(row.get('db_id')), norm(q))
            indexes.setdefault(key, []).append((i, row))
        originals=[]; matched_roles=0; unmatched=[]; duplicates=[]
        for qid, case in cases.items():
            key=(norm(case['db_id']), norm(case['original_question']))
            hits=indexes.get(key, [])
            if not hits:
                unmatched.append(qid); continue
            if len(hits)>1: duplicates.append({'question_id':qid,'matches':len(hits)})
            for _, source in hits:
                base=deepcopy(source)
                md=base.setdefault('metadata', {})
                md.update({'flat_question_id': int(qid), 'reference_question':case['original_question'],
                           'manual_review_mode': certified[(dataset,qid)].get('review_mode'),
                           'manual_source_sha256': certified[(dataset,qid)].get('source_sha256'),
                           'gold_join_evaluation_reference':'Original gold SQL and original-role permission label'})
                # Normalize the two RBAC file schemas used in this repository.
                # Bird/Spider keep the SQL and permission under metadata; LiveSQL
                # keeps them at the row top level (and its SQL is a list).
                if 'gold_sql' not in md and 'gold_sql' in base:
                    md['gold_sql'] = base['gold_sql']
                if 'permission' not in md and 'allowed' in base:
                    md['permission'] = 'allowed' if base['allowed'] else 'denied'
                originals.append(base); matched_roles += 1
                for level in (0,1,2):
                    lev=next(x for x in case['levels'] if x['level']==level)
                    out=deepcopy(base); out['input']=lev['text']
                    out['metadata'].update({'flat_level':level,
                        'effective_retention_ratio':case.get('effective_retention_ratios',[None]*3)[level],
                        'level_selected_ids':lev.get('selected_ids',[])})
                    all_levels[level].append(out)
        all_original.extend(originals)
        manifest['datasets'][dataset]={'certified_cases':len(cases),'matched_role_rows':matched_roles,
          'rows_per_level':matched_roles,
          'role_file':str(role_path.relative_to(ROOT)), 'permission_counts':dict(Counter((r.get('metadata',{}).get('permission') or r.get('allowed')) for r in originals))}
        manifest['unmatched'][dataset]=unmatched; manifest['duplicate_matches'][dataset]=duplicates
    for name, value in [('original.json',all_original),('level0.json',all_levels[0]),('level1.json',all_levels[1]),('level2.json',all_levels[2])]:
        (args.out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    manifest.update({'certified_total':sum(x['certified_cases'] for x in manifest['datasets'].values()),
      'original_rows':len(all_original),'rows_by_level':{str(k):len(v) for k,v in all_levels.items()},
      'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [QUEUE/'manual_certified.json',*ROLE_FILES.values()]},
      'note':'Gold preserves original RBAC permission labels and SQL; levels are disclosure variants, not independently re-labeled permission truth.'})
    (args.out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(manifest,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
