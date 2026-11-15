"""Split original BIRD records with traceable development-exposure exclusions."""
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
import random
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'artifacts/bird300_question_sql_audit_20261009/official_dev_20251106.json'
OUT = ROOT/'data/selected/bird/original_1534_split_seed42'
DIFFICULTIES = ('simple','moderate','challenging')


def main():
    raw = SOURCE.read_bytes()
    rows = json.loads(raw)
    indexed = {r['question_id']:r for r in rows}
    assert len(indexed) == len(rows) == 1534
    normalize = lambda s: re.sub(r'\s+',' ',s.strip()).casefold()
    by_text = defaultdict(set)
    for r in rows:
        by_text[normalize(r['question'])].add(r['question_id'])
    exposure = defaultdict(set)
    errors = []
    # Conservatively reserve all previously audited 300 originals, not only
    # those known to have motivated a particular prompt change.
    audit = ROOT/'artifacts/bird300_question_sql_audit_20261009/cases.json'
    for r in json.loads(audit.read_text(encoding='utf8')):
        for qid in by_text.get(normalize(r['original_question']),set()):
            if indexed[qid]['db_id'] == r['db_id']:
                exposure[qid].add(str(audit.relative_to(ROOT)))
    prefixes = ('flat_disclosure','request_disclosure','request_normalization','clause_frame')
    scanned = []
    def walk(obj, path):
        if isinstance(obj,dict):
            qid = obj.get('question_id')
            db = obj.get('db_id')
            if isinstance(qid,int) and qid in indexed and db == indexed[qid]['db_id']:
                exposure[qid].add(path)
            for value in obj.values():
                walk(value,path)
        elif isinstance(obj,list):
            for value in obj:
                walk(value,path)
        elif isinstance(obj,str):
            for qid in by_text.get(normalize(obj),set()):
                exposure[qid].add(path)
    for directory in sorted((ROOT/'artifacts').iterdir()):
        if directory.is_dir() and directory.name.startswith(prefixes):
            for path in sorted(directory.rglob('*.json')):
                relative = str(path.relative_to(ROOT))
                try:
                    walk(json.loads(path.read_text(encoding='utf8')),relative)
                    scanned.append(relative)
                except (OSError,ValueError) as exc:
                    errors.append({'path':relative,'error':str(exc)})
    if errors:
        raise ValueError(f'Exposure scan incomplete: {errors}')
    # Group exact normalized questions in the same DB. Cross-wording semantic
    # near-duplicates require a separate audit and are not claimed resolved.
    groups = defaultdict(list)
    for r in rows:
        groups[(r['db_id'],normalize(r['question']))].append(r)
    targets = {}
    for d in DIFFICULTIES:
        n = sum(r['difficulty']==d for r in rows)
        forced = sum(r['difficulty']==d for group in groups.values()
                     if any(x['question_id'] in exposure for x in group) for r in group)
        dev = max(round(n*.2),forced)
        val = round(n*.2)
        targets[d] = {'development':dev,'validation':val,'test':n-dev-val}
        if targets[d]['test'] < 0:
            raise ValueError('Historical exposure leaves insufficient evaluation rows')
    splits = {s:[] for s in ('development','validation','test')}
    counts = {s:Counter() for s in splits}
    dbcounts = {s:Counter() for s in splits}
    allocation = {}
    def assign(group, split):
        for row in group:
            splits[split].append(row)
            counts[split][row['difficulty']] += 1
            dbcounts[split][(row['difficulty'],row['db_id'])] += 1
            allocation[row['question_id']] = split
    remaining = []
    for key,group in sorted(groups.items()):
        if any(r['question_id'] in exposure for r in group):
            assign(group,'development')
        else:
            remaining.append(group)
    random.Random(42).shuffle(remaining)
    # Larger groups first; allocate to remaining difficulty quotas, breaking
    # ties toward underrepresented databases. Never split an original group.
    remaining.sort(key=lambda g:-len(g))
    source_db = Counter((r['difficulty'],r['db_id']) for r in rows)
    source_d = Counter(r['difficulty'] for r in rows)
    for group in remaining:
        d,db = group[0]['difficulty'],group[0]['db_id']
        def score(s):
            deficit = targets[d][s]-counts[s][d]
            expected = source_db[d,db]*targets[d][s]/source_d[d]
            return (deficit/max(1,targets[d][s]),expected-dbcounts[s][d,db])
        assign(group,max(splits,key=score))
    assert set(allocation)==set(indexed)
    assert all(allocation[qid]=='development' for qid in exposure)
    assert all(len({allocation[r['question_id']] for r in g})==1 for g in groups.values())
    OUT.mkdir(parents=True,exist_ok=False)
    def save(name,data):
        (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    for split,records in splits.items():
        records.sort(key=lambda r:r['question_id'])
        save(f'{split}.json',records)
        save(f'{split}_generation_input.json',[{k:r[k] for k in ('question_id','db_id','question','difficulty')} for r in records])
    save('assignments.json',[{'question_id':qid,'split':allocation[qid]} for qid in sorted(allocation)])
    save('exposure.json',{str(qid):sorted(paths) for qid,paths in sorted(exposure.items())})
    manifest = {'source':str(SOURCE.relative_to(ROOT)),'source_sha256':hashlib.sha256(raw).hexdigest(),
        'seed':42,'requested_ratio':{'development':.2,'validation':.2,'test':.6},
        'targets_by_difficulty':targets,'actual_by_split_difficulty':{s:dict(c) for s,c in counts.items()},
        'split_counts':{s:len(r) for s,r in splits.items()},'historically_exposed_count':len(exposure),
        'scanned_artifacts':scanned,'scan_errors':errors,
        'duplicate_groups':[[r['question_id'] for r in g] for g in groups.values() if len(g)>1],
        'limitations':['Official BIRD dev re-split for this project, not official BIRD test.',
                      'Historical exclusion covers the prior 300-query audit and saved clause/disclosure/normalization JSON artifacts; cannot prove all other historical exposure absent.',
                      'Exact normalized duplicate grouping only; semantic near-duplicate exclusion is not guaranteed.',
                      'All generated variants and permissions for an original must inherit assignments.json.',
                      'Databases are shared across splits; this is not an unseen-database evaluation.']}
    save('manifest.json',manifest)
    lines=['# 原始 BIRD 1534 条划分','',
        '“开放集”按此前讨论解释为开发集（development）。来源为官方 dev_20251106；不是官方 train/test。',
        '', '目标 20% 开发／20% 验证／60% 测试；历史已使用原题强制进入开发集，超出目标时从测试额度扣除。按难度分层，数据库分布尽量兼顾。',
        '', '| 划分 | simple | moderate | challenging | 合计 |','| --- | ---: | ---: | ---: | ---: |']
    for s in splits:
        lines.append('| '+s+' | '+' | '.join(str(counts[s][d]) for d in DIFFICULTIES)+f' | {len(splits[s])} |')
    lines += ['',f'历史已使用原题 {len(exposure)} 条，全部进入开发集，逐题依据见 exposure.json。',
        '', '每个原始记录只属于一个集合。所有模糊等级、改写和权限配置必须继承 assignments.json，不得再次随机划分。',
        '', '完整 JSON 保留原始 SQL/evidence；*_generation_input.json 不包含 SQL/evidence。没有启动生成 API。',
        '', '验证集用于选方案；测试集在方法冻结后使用。已按数据库内规范化原题文本合并完全重复项；未保证语义近重复或未保存的历史使用全部排除。',
        '当前为已见数据库的新需求评估，不是跨数据库泛化实验。实际计数和来源哈希见 manifest.json。']
    (OUT/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    print(json.dumps({k:manifest[k] for k in ('split_counts','actual_by_split_difficulty','historically_exposed_count','duplicate_groups')},ensure_ascii=False))


if __name__=='__main__':
    main()
