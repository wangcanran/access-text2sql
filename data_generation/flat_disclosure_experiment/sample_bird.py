"""Deterministic difficulty-stratified sampling of original BIRD annotations."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
DIFFICULTIES = ('simple', 'moderate', 'challenging')


def sample(rows, per_difficulty, seed, allow_shortfall=False):
    if per_difficulty < 1:
        raise ValueError('per_difficulty must be positive')
    ids = [r['question_id'] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate source question IDs')
    if any(r['difficulty'] not in DIFFICULTIES for r in rows):
        raise ValueError('unknown difficulty label')
    selected = []
    for difficulty in DIFFICULTIES:
        groups = defaultdict(list)
        for row in rows:
            if row['difficulty'] == difficulty:
                groups[row['db_id']].append(row)
        available = sum(map(len, groups.values()))
        if available < per_difficulty and not allow_shortfall:
            raise ValueError(f'insufficient examples for {difficulty}')
        target = min(available, per_difficulty)
        rng = random.Random(f'{seed}:{difficulty}')
        databases = sorted(groups)
        for db in databases:
            groups[db].sort(key=lambda r: r['question_id'])
            rng.shuffle(groups[db])
        rng.shuffle(databases)
        chosen = []
        while len(chosen) < target:
            for db in databases:
                if groups[db]:
                    chosen.append(groups[db].pop())
                    if len(chosen) == target:
                        break
        selected.extend(chosen)
    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=ROOT/'artifacts/bird300_question_sql_audit_20261009/official_dev_20251106.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--per-difficulty', type=int, default=20)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--allow-shortfall', action='store_true', help='Take all available rows in undersized strata; never duplicate records')
    args = parser.parse_args()
    source_bytes = args.input.read_bytes()
    rows = json.loads(source_bytes)
    selected = sample(rows, args.per_difficulty, args.seed, args.allow_shortfall)
    # Never overwrite an earlier sample or silently combine different runs.
    args.output.mkdir(parents=True, exist_ok=False)
    def save(name, data):
        (args.output/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    save('records.json', selected)
    save('generation_input.json', [{k:r[k] for k in ('question_id','db_id','question','difficulty')} for r in selected])
    counts = {d:dict(sorted(Counter(r['db_id'] for r in selected if r['difficulty']==d).items())) for d in DIFFICULTIES}
    prior_ids = set()
    for path in (ROOT/'artifacts/flat_disclosure_smoke').glob('*/[0-9]*.json'):
        try:
            record = json.loads(path.read_text(encoding='utf8'))
            if 'question_id' in record:
                prior_ids.add(record['question_id'])
        except (ValueError, OSError):
            continue
    manifest = {
        'source_file':str(args.input.resolve()),
        'source_sha256':hashlib.sha256(source_bytes).hexdigest(),
        'source_count':len(rows), 'source_difficulty_counts':dict(Counter(r['difficulty'] for r in rows)),
        'seed':args.seed,'per_difficulty':args.per_difficulty,'sample_count':len(selected),
        'allow_shortfall':args.allow_shortfall,
        'shortfall_by_difficulty':{d:max(0,args.per_difficulty-sum(counts[d].values())) for d in DIFFICULTIES},
        'method':'Equal allocation by official difficulty; randomized round-robin database allocation within each stratum, without replacement.',
        'sample_by_difficulty_database':counts,
        'question_ids':[r['question_id'] for r in selected],
        'previous_smoke_question_ids':sorted(r['question_id'] for r in selected if r['question_id'] in prior_ids),
        'notes':['Original records retained verbatim; no filtering by gold SQL agreement or previous generation success.',
                 'Balanced diagnostic sample, not a proportionate sample for estimating original BIRD population performance.',
                 'BIRD difficulty is not a label for ambiguity or disclosure generation difficulty.',
                 'Prior smoke overlap is reported; this sample is not claimed as an untouched test set.']}
    save('manifest.json',manifest)
    lines = ['# BIRD 按难度分层抽样', '',
             f'原始 dev_20251106 标注 {len(rows)} 条；抽取 {len(selected)} 条，每档目标 {args.per_difficulty} 条；seed={args.seed}。',
             f'实际分层数量：{ {d:sum(counts[d].values()) for d in DIFFICULTIES} }。不足的层取全部，不重复补齐。',
             '', '按官方难度等额分层，层内对数据库随机轮转分配，无放回抽样。适用于对照实验，不代表原数据自然比例。',
             'records.json 完整保留原始记录；generation_input.json 只含题号、数据库、自然语言和难度，不含 SQL/evidence。',
             '', '| 数据库 | simple | moderate | challenging | 合计 |','| --- | ---: | ---: | ---: | ---: |']
    for db in sorted({r['db_id'] for r in rows}):
        values = [counts[d].get(db,0) for d in DIFFICULTIES]
        lines.append('| '+db+' | '+' | '.join(map(str,values+[sum(values)]))+' |')
    lines += ['',f'与历史 flat smoke 重叠的题号：{manifest["previous_smoke_question_ids"]}。不声称是完全未见测试集。',
              '', '## 全部抽样原题']
    for d in DIFFICULTIES:
        lines += ['',f'### {d}','']
        for r in selected:
            if r['difficulty']==d:
                lines.append(f'- **{r["question_id"]} / {r["db_id"]}**：{r["question"]}')
    (args.output/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    print(json.dumps({'output':str(args.output.resolve()),'count':len(selected),'by_database':counts,
                      'previous_smoke_overlap':len(manifest['previous_smoke_question_ids'])},ensure_ascii=False))


if __name__ == '__main__':
    main()
