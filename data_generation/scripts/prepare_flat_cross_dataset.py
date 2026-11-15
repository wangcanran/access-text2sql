"""Prepare matched LiveSQL Query and Spider dev inputs for flat disclosure."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import re


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sample_by_database(cases, count, seed):
    groups = defaultdict(list)
    for case in cases:
        groups[case['db_id']].append(case)
    if len(cases) < count:
        raise ValueError(f'Only {len(cases)} eligible cases for target {count}')
    rng = random.Random(seed)
    for rows in groups.values():
        rows.sort(key=lambda row: row['source_id'])
        rng.shuffle(rows)
    databases = sorted(groups)
    rng.shuffle(databases)
    selected = []
    while len(selected) < count:
        for db_id in databases:
            if groups[db_id]:
                selected.append(groups[db_id].pop())
                if len(selected) == count:
                    break
    return selected


def livesql_cases():
    original = DATA / 'livesqlbench-full-postgresql/livesqlbench_data.jsonl'
    rbac = DATA / 'selected/livesqlbench-full/crud_rbac_dataset_v3_no_sm.gt.json'
    rows = [json.loads(line) for line in original.read_text(encoding='utf8').splitlines() if line.strip()]
    permissions = defaultdict(list)
    for row in read_json(rbac):
        permissions[row['instance_id']].append(row)
    cases, excluded = [], []
    for row in rows:
        if row['category'] != 'Query':
            continue
        role_rows = permissions[row['instance_id']]
        if not role_rows or any(p['question'] != row['query'] or p['db_id'] != row['selected_database'] for p in role_rows):
            excluded.append({'source_id': row['instance_id'], 'reason': 'original_request_differs_from_rbac'})
            continue
        if len({p['role'] for p in role_rows}) != len(role_rows):
            raise ValueError(f'Duplicate role: {row["instance_id"]}')
        cases.append(dict(source_id=row['instance_id'], db_id=row['selected_database'],
                          question=row['query'], rbac=role_rows))
    return cases, (original, rbac), excluded


def spider_cases():
    original = DATA / 'spider/spider_data/dev.json'
    rbac = DATA / 'selected/spider/column_level_rbac_dataset_spider_v3_no_sm.json'
    rows = read_json(original)
    permissions = defaultdict(list)
    for row in read_json(rbac):
        permissions[(row['db_id'], row['input'])].append(row)
    cases = []
    for index, row in enumerate(rows):
        role_rows = permissions[(row['db_id'], row['question'])]
        if not role_rows or len({p['role'] for p in role_rows}) != len(role_rows):
            raise ValueError(f'Missing or duplicate RBAC roles: Spider dev {index}')
        cases.append(dict(source_id=index, db_id=row['db_id'], question=row['question'],
                          rbac=role_rows))
    return cases, (original, rbac), []


def spider_schemas(db_ids):
    path = DATA / 'spider/spider_data/tables.json'
    schemas = {}
    for source in read_json(path):
        if source['db_id'] not in db_ids:
            continue
        tables = {name: {'columns': [], 'foreign_keys': []}
                  for name in source['table_names_original']}
        columns = source['column_names_original']
        for index, (table_index, name) in enumerate(columns):
            if table_index >= 0:
                tables[source['table_names_original'][table_index]]['columns'].append(
                    {'name': name, 'type': source['column_types'][index]})
        for fk_index, (from_index, to_index) in enumerate(source['foreign_keys']):
            from_table, from_name = columns[from_index]
            to_table, to_name = columns[to_index]
            tables[source['table_names_original'][from_table]]['foreign_keys'].append(
                {'id': fk_index, 'seq': 0, 'from': from_name,
                 'table': source['table_names_original'][to_table], 'to': to_name})
        schemas[source['db_id']] = {'tables': tables, 'descriptions': {}}
    if set(schemas) != db_ids:
        raise ValueError(f'Missing Spider schemas: {sorted(db_ids - set(schemas))}')
    return schemas, [path]


def livesql_schemas(db_ids):
    schemas, paths = {}, []
    root = DATA / 'livesqlbench-full-postgresql'
    for db_id in sorted(db_ids):
        schema_path = root / db_id / f'{db_id}_schema.txt'
        meanings_path = root / db_id / f'{db_id}_column_meaning_base.json'
        paths.extend([schema_path, meanings_path])
        meanings = read_json(meanings_path)
        tables = defaultdict(lambda: {'columns': [], 'foreign_keys': []})
        descriptions = defaultdict(lambda: {'rows': []})
        for key, meaning in meanings.items():
            parts = key.split('|')
            if len(parts) != 3 or parts[0] != db_id:
                raise ValueError(f'Invalid column meaning key: {key}')
            _, table, column = parts
            description = (meaning.get('column_meaning', '') if isinstance(meaning, dict)
                           else meaning)
            match = re.match(r'([A-Za-z][A-Za-z0-9_]*(?:\([^)]*\))?)\.', description)
            tables[table]['columns'].append({'name': column,
                                              'type': match.group(1) if match else
                                                      ('JSONB' if isinstance(meaning, dict) else 'unknown')})
            descriptions[table]['rows'].append({'original_column_name': column,
                                                'column_description': description,
                                                'value_description': json.dumps(meaning['fields_meaning'], ensure_ascii=False)
                                                    if isinstance(meaning, dict) and 'fields_meaning' in meaning else ''})
        current_table = None
        fk_index = 0
        for raw_line in schema_path.read_text(encoding='utf8').splitlines():
            line = raw_line.strip()
            create = re.match(r'CREATE TABLE\s+"?([^"\s(]+)"?\s*\(', line, re.I)
            if create:
                current_table = create.group(1)
                continue
            if line.startswith(');'):
                current_table = None
                continue
            if not current_table or not line.upper().startswith('FOREIGN KEY'):
                continue
            fk = re.match(r'FOREIGN KEY\s*\(([^)]+)\)\s*REFERENCES\s+"?([^"\s(]+)"?\s*\(([^)]+)\)', line, re.I)
            if not fk:
                raise ValueError(f'Unparsed LiveSQL foreign key: {db_id}: {line}')
            from_columns = [part.strip(' "') for part in fk.group(1).split(',')]
            to_columns = [part.strip(' "') for part in fk.group(3).split(',')]
            if len(from_columns) != len(to_columns):
                raise ValueError(f'Foreign key arity mismatch: {db_id}: {line}')
            for sequence, (from_column, to_column) in enumerate(zip(from_columns, to_columns)):
                tables[current_table]['foreign_keys'].append({
                    'id': fk_index, 'seq': sequence, 'from': from_column,
                    'table': fk.group(2), 'to': to_column})
            fk_index += 1
        if not tables or not schema_path.is_file():
            raise ValueError(f'Missing LiveSQL schema: {db_id}')
        schemas[db_id] = {'tables': dict(tables), 'descriptions': dict(descriptions)}
    return schemas, paths


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def prepare(name, count, seed, output):
    cases, sources, excluded = livesql_cases() if name == 'livesql' else spider_cases()
    selected = sample_by_database(cases, count, f'{seed}:{name}')
    schema_ids = {case['db_id'] for case in selected}
    schemas, schema_paths = (livesql_schemas(schema_ids) if name == 'livesql'
                             else spider_schemas(schema_ids))
    folder = output / name
    folder.mkdir(parents=True, exist_ok=False)
    generation = []
    role_index = []
    rbac_rows = []
    for question_id, case in enumerate(selected):
        generation.append({k: v for k, v in {'question_id': question_id,
                            'db_id': case['db_id'], 'question': case['question']}.items()})
        role_index.append({'question_id': question_id, 'source_id': case['source_id'],
                           'db_id': case['db_id'], 'roles': sorted(p['role'] for p in case['rbac'])})
        for row in case['rbac']:
            rbac_rows.append({'question_id': question_id, 'source_id': case['source_id'],
                              'rbac_record': row})
    write_json(folder / 'generation_input.json', generation)
    write_json(folder / 'schemas.json', schemas)
    write_json(folder / 'role_index.json', role_index)
    write_json(folder / 'rbac_records.json', rbac_rows)
    manifest = {'dataset': name, 'sample_count': len(selected), 'seed': seed,
                'eligible_source_count': len(cases), 'excluded_sources': excluded,
                'sampling': 'Randomized database round-robin without replacement; independent of RBAC labels',
                'selection': 'Original category == Query' if name == 'livesql' else 'Spider dev with RBAC match',
                'levels_per_case': 3, 'role_instances': len(rbac_rows),
                'role_count_distribution': dict(sorted(Counter(len(c['rbac']) for c in selected).items())),
                'database_count': len(schema_ids),
                'source_sha256': {str(p.relative_to(ROOT)): sha256(p) for p in [*sources, *schema_paths]}}
    write_json(folder / 'manifest.json', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--per-dataset', type=int, default=300)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.per_dataset < 1 or args.output.exists():
        parser.error('Count must be positive and output must not exist')
    args.output.mkdir(parents=True)
    result = [prepare(name, args.per_dataset, args.seed, args.output)
              for name in ('livesql', 'spider')]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
