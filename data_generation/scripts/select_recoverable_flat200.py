"""Prescreen flat inputs for recoverable ambiguity, retaining audit features."""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    'bird': ROOT / 'flat_disclosure_experiment/outputs/bird_test_3levels/completed',
    'livesql': ROOT / 'flat_disclosure_experiment/outputs/livesql_300_seed42_cross_v1/completed',
    'spider': ROOT / 'flat_disclosure_experiment/outputs/spider_300_seed42_cross_v1/completed',
}
STOP = set('a an the of for to in on and or by with from at is are was were be been this that these those each every all one two three it its their his her what which who whose how much many please show list give return find tell me us data information details relevant appropriate specified specific certain defined particular some single chosen selected corresponding related associated records entities values attribute attributes field fields result results'.split())
CUES = ('specified', 'specific', 'defined', 'particular', 'chosen', 'which',
        'what', 'threshold', 'range', 'period', 'date', 'year', 'name', 'type',
        'category', 'field', 'attribute', 'measure', 'metric', 'criterion',
        'condition', 'value', 'amount', 'number', 'count', 'average', 'ratio',
        'percentage', 'rank', 'top', 'highest', 'lowest', 'before', 'after',
        'within', 'group', 'based on')
VAGUE = ('appropriate', 'relevant', 'certain', 'some', 'thing', 'information',
         'details', 'data', 'entity', 'entities', 'metric', 'measure',
         'attribute', 'criterion', 'condition', 'specified', 'chosen')


def tokens(text):
    return set(re.findall('[a-z][a-z0-9]+', text.lower())) - STOP


def count_terms(text, terms):
    text = text.lower()
    return sum(len(re.findall('(?<![a-z])' + re.escape(term) + '(?![a-z])', text))
               for term in terms)


def features(trace):
    source = tokens(trace['question'])
    texts = [row['rewritten_request'] for row in trace['levels'][:2]]
    anchors = [round(len(source & tokens(text)) / max(1, len(source)), 4) for text in texts]
    cues = [count_terms(text, CUES) for text in texts]
    vague = [count_terms(text, VAGUE) for text in texts]
    score = round(4*anchors[0] + 2*anchors[1] + .8*min(cues[0], 4)
                  + .5*min(cues[1], 4) - .3*vague[0] - .2*vague[1], 4)
    flags = []
    if anchors[0] < .22:
        flags.append('level0_lost_original_anchors')
    if cues[0] < 2:
        flags.append('level0_few_question_cues')
    if vague[0] >= 5:
        flags.append('level0_excess_generic_terms')
    if len(texts[0].split()) < 8:
        flags.append('level0_too_short')
    return score, flags, dict(anchor_retention=anchors, question_cues=cues,
                              generic_terms=vague)


def spider_difficulties():
    base = ROOT / 'flat_disclosure_experiment/inputs/livesql_spider_300_seed42/spider'
    inputs = json.loads((base / 'generation_input.json').read_text(encoding='utf8'))
    rbac = json.loads((ROOT / 'data/selected/spider/column_level_rbac_dataset_spider_v3_no_sm.json').read_text(encoding='utf8'))
    by_key = {}
    for row in rbac:
        key = row['db_id'], row['input']
        if key in by_key and by_key[key] != row['difficulty']:
            raise ValueError('Inconsistent Spider difficulty')
        by_key[key] = row['difficulty']
    return {row['question_id']: by_key[row['db_id'], row['question']] for row in inputs}


def eligible(name, difficulties):
    rows = []
    for path in sorted(SOURCES[name].glob('*.json')):
        trace = json.loads(path.read_text(encoding='utf8'))['trace']
        levels = trace.get('levels', [])
        if trace.get('status') != 'review_passed' or [r.get('level') for r in levels] != [0, 1, 2]:
            continue
        ratios = [round(r['effective_retention_ratio'], 12) for r in levels]
        if len(set(ratios)) != 3:
            continue
        score, flags, detail = features(trace)
        rows.append(dict(question_id=trace['question_id'], db_id=trace['db_id'],
             difficulty=(difficulties[trace['question_id']] if name == 'spider'
                         else trace.get('difficulty')),
             original_question=trace['question'],
             levels=[dict(level=r['level'], natural_language=r['rewritten_request'],
                          effective_retention_ratio=r['effective_retention_ratio']) for r in levels],
             trace_path=str(path.relative_to(ROOT)), quality_score=score,
             quality_flags=flags, score_features=detail))
    return rows


def select_balanced(pool, count, name):
    groups = {}
    for row in pool:
        groups.setdefault(row['difficulty'], []).append(row)
    for rows in groups.values():
        rows.sort(key=lambda row: (len(row['quality_flags']), -row['quality_score'], row['question_id']))
    if name == 'bird':
        # Preserve nontrivial SQL composition instead of selecting by easy prose.
        floors = {'challenging': 35, 'moderate': 50}
    elif name == 'spider':
        floors = {'extra': 25, 'hard': 35, 'medium': 70}
    else:
        floors = {}
    if sum(floors.values()) > count:
        raise ValueError('Difficulty floors exceed requested case count')
    selected = []
    for label, floor in floors.items():
        selected.extend(groups[label][:floor])
    remaining = [row for row in pool if row['question_id'] not in
                 {item['question_id'] for item in selected}]
    remaining.sort(key=lambda row: (len(row['quality_flags']), -row['quality_score'], row['question_id']))
    selected.extend(remaining[:count-len(selected)])
    return sorted(selected, key=lambda row: row['question_id'])


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--size', type=int, default=200)
    args = parser.parse_args()
    if args.out.exists() or args.size < 1:
        parser.error('Use a new output directory and positive size')
    difficulties = spider_difficulties()
    args.out.mkdir(parents=True)
    summary = {}
    for name in SOURCES:
        pool = eligible(name, difficulties)
        if len(pool) < args.size:
            raise ValueError(f'Not enough {name} candidates')
        selected = select_balanced(pool, args.size, name)
        (args.out / f'{name}_cases.json').write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding='utf8')
        summary[name] = dict(eligible=len(pool), selected=len(selected),
            difficulty=dict(Counter(row['difficulty'] for row in selected)),
            flagged=sum(bool(row['quality_flags']) for row in selected),
            databases=len(set(row['db_id'] for row in selected)),
            score_min=min(row['quality_score'] for row in selected))
    (args.out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
