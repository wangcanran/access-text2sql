"""Normalize semantic answer types before discovering stable question IDs."""
from copy import deepcopy


ANSWER_DIMENSIONS = {
    'entity_value': 'value', 'literal_value': 'value',
    'attribute': 'field', 'operation': 'operation',
    'entity_kind': 'scope', 'relationship': 'scope',
}


def normalize_choices(raw):
    raw = deepcopy(raw)
    if not isinstance(raw, dict) or not isinstance(raw.get('fragments'), list):
        raise ValueError('Expected fragments for typed source questions')
    for fragment in raw['fragments']:
        if not isinstance(fragment, dict) or not isinstance(fragment.get('open_choices', []), list):
            raise ValueError('Each fragment needs an open_choices array')
        for question in fragment.get('open_choices', []):
            if not isinstance(question, dict):
                raise ValueError('Each open choice needs an answer_kind')
            kind = question.pop('answer_kind', None)
            if kind not in ANSWER_DIMENSIONS:
                raise ValueError('Each source open choice needs a valid answer_kind')
            question['dimension'] = ANSWER_DIMENSIONS[kind]
    return raw


def normalize_questions(row):
    """The kind controls routing; legacy dimension is not a second authority."""
    if not isinstance(row, dict):
        raise ValueError('Each item needs question metadata')
    kinds = row.pop('answer_kinds', None)
    focus = row.get('question_focus', {})
    if (not isinstance(kinds, dict) or not isinstance(focus, dict)
            or set(kinds) != set(focus)):
        raise ValueError('answer_kinds must account for every question_focus key')
    normalized = {}
    for dimension, text in focus.items():
        kind = kinds[dimension]
        if kind not in ANSWER_DIMENSIONS:
            raise ValueError('Unknown answer kind')
        normalized.setdefault(ANSWER_DIMENSIONS[kind], []).append(text)
    row['question_focus'] = {dim: ' '.join(dict.fromkeys(texts)) for dim, texts in normalized.items()}
    row['missing'] = list(row['question_focus'])
    if 'field' not in row['missing']:
        row['field_candidates'] = []
    return row
