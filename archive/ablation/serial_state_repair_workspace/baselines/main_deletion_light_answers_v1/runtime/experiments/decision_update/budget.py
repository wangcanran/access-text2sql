"""Opt-in lossless schema packing, staged descriptions and strict decision memo.

Full code-owned schema/authorization is never filtered. Candidate discovery
keeps descriptions for entire schema-connected components, not permitted ones.
Update steps may request a one-shot expansion to the full description catalog.
"""
from copy import deepcopy
import hashlib
import json

from .hybrid import HybridUpdate

VERSION = 'staged_schema_exact_memo_v1'
SCHEMA_NOTE = '''
schema.tables maps each column to [type,description] or [type] when its description
is omitted from this step; all identifiers are present. FK rows are
[from_table.column,to_table.column,constraint_id,sequence]. Descriptions omitted
from this step do not imply absence or lack of access. If their meaning is needed,
return only {"schema_request":true}; the complete meanings will be provided once.
'''


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def compact_meaning(text):
    # Exact duplicate segments only. Do not truncate definitions, units or enums.
    return ' | '.join(dict.fromkeys(text.split(' | ')))


def column_refs(value, known):
    if isinstance(value, str):
        return {value} & known
    if isinstance(value, dict):
        return set().union(*(column_refs(v, known) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(column_refs(v, known) for v in value)) if value else set()
    return set()


def schema_view(context, payload, stage, full=False):
    columns = {t['name'] + '.' + c['name']: (t['name'], c)
               for t in context['tables'] for c in t['columns']}
    all_tables = {t['name'] for t in context['tables']}
    bare = stage.removesuffix('_contract_retry')
    roots = set()
    refs = column_refs({k: v for k, v in payload.items()
                        if k not in ('schema', 'column_meanings')}, set(columns))
    roots.update(columns[c][0] for c in refs)
    source = payload.get('source', {})
    roots.update(source.get('tables', []))
    for node in payload.get('nodes', []) + payload.get('editable_nodes', []) + payload.get('readonly_nodes', []):
        roots.update(node.get('tables', []))
    if full or bare.startswith('progressive_') or bare == 'scope_decision' or not roots:
        selected = set(columns)
    else:
        detailed_tables = roots & all_tables
        if bare in ('field_decision', 'permission_decision'):
            # Field domains may live several joins away. Keep the full connected
            # component regardless of grants; no permission-biased schema pruning.
            while True:
                old = set(detailed_tables)
                for e in context['foreign_keys']:
                    pair = {e['from_table'], e['to_table']}
                    if detailed_tables & pair:
                        detailed_tables |= pair
                if old == detailed_tables:
                    break
        selected = refs | {c for c, (t, _) in columns.items() if t in detailed_tables}
    schema = {'tables': {t['name']: {c['name']: [c['type']] +
        ([compact_meaning(context.get('column_language', {}).get(t['name'] + '.' + c['name'], ''))]
         if t['name'] + '.' + c['name'] in selected else []) for c in t['columns']}
                         for t in context['tables']},
        'foreign_keys': [[e['from_table'] + '.' + e['from_column'],
                          e['to_table'] + '.' + e['to_column'],
                          e.get('constraint_id'), e.get('sequence')]
                         for e in context['foreign_keys']]}
    return schema, selected, set(columns)


class BudgetUpdate(HybridUpdate):
    def __init__(self, *, pack=True, memo=True):
        super().__init__()
        self.pack, self.memo = pack, memo
        self._decisions = {}
        self._menu_guard = None
        self.budget_audit = []

    def prepare_menu(self, call, session, plan, context, max_options=5):
        old = self._menu_guard
        # Conservative invalidation includes ALL graph meaning/edges, permission,
        # categories and user history. Do not reuse across changed scope/relations.
        self._menu_guard = fingerprint({k: session.get(k) for k in
            ('view', 'allowed', 'turns', 'original_request', 'current_request', 'permission_issues',
             'db_name', 'menu_frontiers', 'public_interactions')})
        try:
            result = super().prepare_menu(call, session, plan, context, max_options)
            result[0]['budget_audit'] = deepcopy(self.budget_audit)
            return result
        finally:
            self._menu_guard = old

    def _packed_call(self, call, context):
        if not self.pack:
            return call
        def packed(stage, prompt, payload):
            if not isinstance(payload, dict):
                return call(stage, prompt, payload)
            outgoing = deepcopy(payload)
            active = outgoing['input'] if stage.endswith('_contract_retry') else outgoing
            if 'schema' not in active:
                return call(stage, prompt, payload)
            original_chars = len(json.dumps(active['schema'], ensure_ascii=False)) + len(
                json.dumps(active.get('column_meanings', {}), ensure_ascii=False))
            schema, detailed, known = schema_view(context, active, stage)
            active['schema'] = schema
            active.pop('column_meanings', None)
            entry = {'stage': stage, 'original_schema_chars': original_chars,
                'packed_schema_chars': len(json.dumps(schema, ensure_ascii=False)),
                'detailed_columns': len(detailed), 'catalog_columns': len(known), 'expanded': False}
            self.budget_audit.append(entry)
            raw = call(stage, prompt + SCHEMA_NOTE, outgoing)
            if (isinstance(raw, dict) and raw.get('schema_request') is True
                    or column_refs(raw, known) - detailed):
                entry['expanded'] = True
                active['schema'] = schema_view(context, active, stage, full=True)[0]
                raw = call(stage + '_schema_expansion', prompt + SCHEMA_NOTE +
                    '\nAll descriptions are now supplied. Return the requested task result.', outgoing)
            return raw
        return packed

    def compile_decision(self, call, payload, context, *, field, scope=False):
        key = fingerprint({'payload': payload, 'context': context, 'field': field,
                           'scope': scope, 'menu_guard': self._menu_guard})
        if self.memo and key in self._decisions:
            self.budget_audit.append({'stage': 'decision_memo', 'hit': True, 'key': key})
            return deepcopy(self._decisions[key])
        self.budget_audit.append({'stage': 'decision_memo', 'hit': False, 'key': key})
        result = super().compile_decision(self._packed_call(call, context), payload, context,
                                          field=field, scope=scope)
        if self.memo:
            self._decisions[key] = deepcopy(result)
        return result

    def analyze_flat(self, call, payload, context):
        return super().analyze_flat(self._packed_call(call, context), payload, context)

    def _transaction(self, call, session, base, event, context):
        result = super()._transaction(self._packed_call(call, context), session, base, event, context)
        result['budget_audit'] = deepcopy(self.budget_audit)
        return result
