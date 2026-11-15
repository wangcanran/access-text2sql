"""Align schema documentation keys before candidate generation, not policies."""
from copy import deepcopy

from .menu_candidates import FixedMenuMethod

VERSION = 'main_v10_menu_candidates_schema_v2'


def aligned_schema_context(context):
    result = deepcopy(context)
    meanings = result.get('column_language', {})
    groups = {}
    for key, meaning in meanings.items():
        if isinstance(key, str) and isinstance(meaning, str) and meaning.strip():
            groups.setdefault(key.casefold(), set()).add(meaning)
    columns = [t['name'] + '.' + c['name'] for t in context['tables'] for c in t['columns']]
    schema_groups = {}
    for column in columns:
        schema_groups.setdefault(column.casefold(), []).append(column)
    for column in columns:
        if meanings.get(column):
            continue  # Exact documentation remains authoritative.
        options = groups.get(column.casefold(), set())
        if len(options) == 1 and len(schema_groups[column.casefold()]) == 1:
            meanings[column] = next(iter(options))
    result['column_language'] = meanings
    return result


class SchemaAlignedMenuMethod(FixedMenuMethod):
    """Candidate bugfix plus schema-description alignment; original prompts."""

    def compile_decision(self, call, payload, context, *, field, scope=False):
        return super().compile_decision(call, payload, aligned_schema_context(context),
                                        field=field, scope=scope)

    def audit(self):
        return dict(super().audit(), version=VERSION, variant='menu_candidates_schema_v2',
                    candidate_description_alignment='unambiguous_casefold_to_schema',
                    structured_schema_prompt=False)
