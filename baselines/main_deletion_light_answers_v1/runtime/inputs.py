"""Project disclosure records to public inputs and private simulator intention."""
import hashlib
import json
from pathlib import Path


def load_source(path, question_id):
    path = Path(path).resolve()
    if path.is_dir():
        return load_selected_dataset(path, question_id)
    data = path.read_bytes()
    raw = json.loads(data.decode('utf8'))
    if raw.get('question_id') != question_id:
        raise ValueError('Source question_id differs from requested ID')
    if any(not isinstance(raw.get(k), str) or not raw[k].strip() for k in ('db_id', 'question')):
        raise ValueError('Source needs database ID and private original question')
    levels = {}
    for row in raw.get('levels', []):
        level = row.get('level')
        if type(level) is not int or level in levels:
            raise ValueError('Disclosure levels must have unique integer IDs')
        text = row.get('rewritten_request')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Disclosure level has no generated request')
        levels[level] = {'rewritten_request': text,
                         **{key: row[key] for key in ('target_retention_ratio', 'effective_retention_ratio')
                            if key in row}}
    if not levels:
        raise ValueError('No disclosure levels')
    return {'question_id': question_id, 'db_id': raw['db_id'], 'question': raw['question'],
            'levels': levels, 'source_path': str(path), 'source_sha256': hashlib.sha256(data).hexdigest(),
            'simulator_intent': _simulator_intent(raw.get('simulator_intent'))}


def load_selected_dataset(path, question_id):
    """Public levels plus private original; never consume hidden level annotations."""
    path = Path(path).resolve()
    names = ('manifest.json', 'cases.json', 'interaction_inputs.jsonl')
    files = {name: (path / name).read_bytes() for name in names}
    manifest = json.loads(files['manifest.json'])
    cases = [row for row in json.loads(files['cases.json']) if row['question_id'] == question_id]
    if len(cases) != 1:
        raise ValueError('Dataset must contain exactly one source case for the requested ID')
    case = cases[0]
    question = case.get('original_question')
    if not isinstance(question, str) or not question.strip():
        raise ValueError('Dataset source needs a private original question')
    levels = {}
    for line in files['interaction_inputs.jsonl'].decode('utf8').splitlines():
        row = json.loads(line)
        if row['question_id'] != question_id:
            continue
        level, text = row['level'], row.get('natural_language')
        if row['db_id'] != case['db_id'] or type(level) is not int or level in levels:
            raise ValueError('Inconsistent or duplicate dataset level')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Dataset level needs natural_language')
        levels[level] = {'rewritten_request': text,
                         **{key: row[key] for key in ('target_retention_ratio', 'effective_retention_ratio')
                            if key in row}}
    if not levels:
        raise ValueError('No interaction inputs for requested case')
    hashes = {name: hashlib.sha256(files[name]).hexdigest() for name in names}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode('utf8')).hexdigest()
    return {'question_id': question_id, 'db_id': case['db_id'], 'question': question,
            'levels': levels, 'source_path': str(path), 'source_sha256': digest,
            'source_file_hashes': hashes, 'source_split': case.get('source_split', manifest.get('source_split')),
            'difficulty': case.get('difficulty'),
            'simulator_intent': _simulator_intent(case.get('simulator_intent')),
            'flat_units': _load_flat_units(path, case)}


def _load_flat_units(dataset_path, case):
    """Read only linguistic modules from the flat-disclosure trace."""
    trace_name = case.get('trace_path')
    if not isinstance(trace_name, str) or not trace_name.strip():
        return []
    root = Path(dataset_path).resolve().parents[3]
    trace_path = root / trace_name.replace('\\', '/')
    if not trace_path.exists():
        return []
    frame = json.loads(trace_path.read_text(encoding='utf8')).get('trace', {}).get('frame', {})
    units = []
    for role in ('outputs', 'modifiers'):
        for row in frame.get(role, []):
            if isinstance(row, dict) and isinstance(row.get('item_id'), str) and isinstance(row.get('span'), str):
                units.append({'item_id': row['item_id'], 'role': role[:-1], 'span': row['span']})
    return units


def _simulator_intent(value):
    """Validate private, human-authored field preferences for the simulated user."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'acceptable_field_groups'}:
        raise ValueError('simulator_intent must contain acceptable_field_groups')
    groups = value['acceptable_field_groups']
    if not isinstance(groups, list) or not groups:
        raise ValueError('acceptable_field_groups must be a nonempty list')
    result = []
    for group in groups:
        if (not isinstance(group, dict)
                or set(group) - {'intent', 'columns', 'target_roles'}
                or not {'intent', 'columns'} <= set(group)
                or not isinstance(group['intent'], str) or not group['intent'].strip()
                or not isinstance(group['columns'], list) or not group['columns']
                or any(not isinstance(c, str) or not c.strip() for c in group['columns'])
                or len(set(group['columns'])) != len(group['columns'])):
            raise ValueError('Each acceptable field group needs an intent and unique column IDs')
        roles = group.get('target_roles', ['output', 'modifier'])
        if (not isinstance(roles, list) or not roles
                or any(role not in ('output', 'modifier') for role in roles)
                or len(set(roles)) != len(roles)):
            raise ValueError('target_roles must be a nonempty unique list of output/modifier')
        result.append({'intent': group['intent'].strip(), 'columns': group['columns'],
                       'target_roles': roles})
    return {'acceptable_field_groups': result}


def load_reference_sql(path, source):
    """Private simulator reference; match ID, database and exact original text."""
    path = Path(path).resolve()
    data = path.read_bytes()
    rows = json.loads(data.decode('utf8'))
    matches = [r for r in rows if r.get('question_id') == source['question_id']]
    if len(matches) != 1:
        raise ValueError('Expected one gold SQL record for question_id')
    row = matches[0]
    if row.get('db_id') != source['db_id'] or row.get('question') != source['question']:
        raise ValueError('Gold SQL database or original question mismatch')
    if not isinstance(row.get('SQL'), str) or not row['SQL'].strip():
        raise ValueError('Gold SQL is missing')
    return {'sql': row['SQL'], 'path': str(path),
            'sha256': hashlib.sha256(data).hexdigest(), 'question_id': row['question_id']}


def intent_columns_for_role(intent, role):
    """Return private acceptable columns whose intent applies to this frame role."""
    if role not in ('output', 'modifier'):
        raise ValueError('role must be output or modifier')
    if not intent:
        return None
    return sorted({column for group in intent['acceptable_field_groups']
                   if role in group.get('target_roles', ['output', 'modifier'])
                   for column in group['columns']})
