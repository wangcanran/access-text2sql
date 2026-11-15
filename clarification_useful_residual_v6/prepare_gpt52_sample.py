"""Freeze a stratified 80-question, three-level, two-role evaluation sample.

Gold SQL is used only to select contrasting policies and for later evaluation.
It is never sent to the clarification agent. The parser resolves direct base-table
columns; its coverage labels are sampling aids, not permission ground truth.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random

from sqlglot import exp, parse_one

from clarification_deferred_deletion.baseline import module


def direct_columns(sql, schema):
    table_names = {name.casefold(): name for name in schema['tables']}
    columns = {table: {c['name'].casefold(): c['name'] for c in spec['columns']}
               for table, spec in schema['tables'].items()}
    tree = parse_one(sql, read='sqlite')
    aliases = {}
    for table in tree.find_all(exp.Table):
        base = table_names.get(table.name.casefold())
        if base:
            aliases[table.alias_or_name.casefold()] = base
    resolved = set()
    for column in tree.find_all(exp.Column):
        name = column.name.casefold()
        if column.table:
            table = aliases.get(column.table.casefold())
            if table and name in columns[table]:
                resolved.add(f"{table}.{columns[table][name]}")
        else:
            matches = [(table, mapping[name]) for table, mapping in columns.items()
                       if name in mapping]
            if len(matches) == 1:
                table, actual = matches[0]
                resolved.add(f"{table}.{actual}")
    return resolved


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=20261028)
    parser.add_argument('--questions', type=int, default=80)
    parser.add_argument('--pilot-questions', type=int, default=12)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Selection directory already exists; refusing to replace it')
    live = module('live')
    cases = json.loads((live.DATA/'cases.json').read_text(encoding='utf8'))
    schemas = json.loads(live.SCHEMAS.read_text(encoding='utf8'))
    roles = json.loads(live.POLICIES.read_text(encoding='utf8'))
    roles_by_db = defaultdict(dict)
    for role in roles:
        roles_by_db[role['db_id']].setdefault(role['role'], role)
    rng = random.Random(args.seed)
    candidates = []
    for case in cases:
        source = live.load_source(live.DATA, case['question_id'])
        reference = live.load_reference_sql(live.GOLD, source)
        try:
            gold_cols = direct_columns(reference['sql'], schemas[source['db_id']])
        except Exception:
            continue
        if not gold_cols:
            continue
        context = live.schema_context(schemas[source['db_id']])
        ranked = []
        for role in roles_by_db[source['db_id']].values():
            allowed = set(live.engine_module('policy').allowed_columns(context, role['policy']))
            missing = gold_cols - allowed
            ranked.append((role, allowed, missing))
        broad = [x for x in ranked if not x[2]]
        restricted = [x for x in ranked if x[2]]
        if not broad or not restricted:
            continue
        positive = max(broad, key=lambda x: (len(x[1]), x[0]['role']))
        negative = max(restricted, key=lambda x: (len(x[2]), -len(x[1]), x[0]['role']))
        candidates.append((case, source, reference, gold_cols, positive, negative))
    # Proportional sampling within database x difficulty, then deterministic
    # allocation of leftover slots. The pilot is the first 12 selected questions.
    strata = defaultdict(list)
    for row in candidates:
        case = row[0]
        strata[(case['db_id'], case['difficulty'])].append(row)
    for group in strata.values():
        rng.shuffle(group)
    target = min(args.questions, len(candidates))
    if target < args.questions:
        raise ValueError(f'Only {len(candidates)} contrasting questions, requested {args.questions}')
    allocation = {key: min(len(group), int(target * len(group) / len(candidates)))
                  for key, group in strata.items()}
    while sum(allocation.values()) < target:
        eligible = [key for key in strata if allocation[key] < len(strata[key])]
        key = max(eligible, key=lambda k: (target * len(strata[k]) / len(candidates)
                                           - allocation[k], k))
        allocation[key] += 1
    selected = [row for key in sorted(strata) for row in strata[key][:allocation[key]]]
    rng.shuffle(selected)
    args.out.mkdir(parents=True)
    fixtures = [[], []]
    summary = []
    for case, source, reference, gold_cols, positive, negative in selected:
        summary.append(dict(question_id=case['question_id'], db_id=case['db_id'],
                            difficulty=case['difficulty'], resolved_gold_column_count=len(gold_cols),
                            broad_role=positive[0]['role'], restricted_role=negative[0]['role'],
                            restricted_missing_count=len(negative[2])))
        for slot, (role, allowed, missing) in enumerate((positive, negative)):
            for level in range(3):
                fixtures[slot].append(dict(question_id=case['question_id'], level=level,
                    db_id=source['db_id'], difficulty=case['difficulty'],
                    role=role['role'], role_slot=('broad' if slot == 0 else 'restricted'),
                    policy_source_question_id=role['metadata']['question_id'],
                    source_sha256=source['source_sha256'],
                    policy_sha256=hashlib.sha256(json.dumps(role['policy'], sort_keys=True).encode()).hexdigest(),
                    allowed_columns=sorted(allowed),
                    gold_columns=sorted(gold_cols), gold_denied_columns=sorted(missing),
                    public_request=source['levels'][level]['rewritten_request'],
                    private_original_for_evaluation_only=source['question'],
                    reference_sql_sha256=reference['sha256']))
    for slot, name in enumerate(('broad', 'restricted')):
        payload = dict(seed=args.seed, design='stratified_database_difficulty_role_contrast',
                       gold_column_resolution='sqlglot_direct_base_columns_lower_bound',
                       expected_semantics_are_private_evaluation_only=True,
                       pilot_question_ids=[r[0]['question_id'] for r in selected[:args.pilot_questions]],
                       cases=fixtures[slot])
        (args.out/f'{name}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf8')
    (args.out/'SAMPLE.json').write_text(json.dumps(dict(seed=args.seed,
        eligible_questions=len(candidates), selected_questions=len(selected),
        selected_sessions=len(selected)*6, pilot_questions=args.pilot_questions,
        strata=dict(Counter(f"{row[0]['db_id']}:{row[0]['difficulty']}" for row in selected)),
        questions=summary), ensure_ascii=False, indent=2), encoding='utf8')
    print(f'Frozen {len(selected)} questions / {len(selected)*6} sessions at {args.out}')
    print('Pilot IDs:', ','.join(str(row[0]['question_id']) for row in selected[:args.pilot_questions]))


if __name__ == '__main__':
    main()
