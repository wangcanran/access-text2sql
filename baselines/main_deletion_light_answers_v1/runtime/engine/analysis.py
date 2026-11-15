"""Independent flat segmentation and validated linguistic/schema annotations."""
import json
from ..model import parse_query_frame
from ..source_prompts import DISCLOSURE_FRAME_PROMPT, ROLE_PROMPT
from ..model import QueryFrame
from .prompts import RELATIONS, GROUNDING


def inventory(frame):
    """All literal occurrences are evidence; never guess a unique interval."""
    items = []
    for kind, rows in (('output', frame.outputs), ('modifier', frame.modifiers)):
        for row in rows:
            positions, offset = [], 0
            while True:
                start = frame.original_text.find(row['span'], offset)
                if start < 0:
                    break
                positions.append([start, start + len(row['span'])])
                offset = start + 1
            items.append({**row, 'kind': kind, 'positions': positions})
    return {'items': items}


def apply_roles(frame, raw):
    ids = {i['item_id'] for i in frame.outputs + frame.modifiers}
    if not isinstance(raw, dict) or set(raw) != {'roles'} or not isinstance(raw['roles'], list):
        raise ValueError('Expected roles array')
    roles = {}
    for row in raw['roles']:
        if (not isinstance(row, dict) or set(row) != {'item_id', 'role'}
                or row['item_id'] not in ids or row['item_id'] in roles
                or row['role'] not in ('output', 'modifier')):
            raise ValueError('Invalid role or duplicate ID')
        roles[row['item_id']] = row['role']
    if set(roles) != ids:
        raise ValueError('Roles must cover every item exactly once')
    buckets, changes = {'output': [], 'modifier': []}, []
    for entry in inventory(frame)['items']:
        iid = entry['item_id']
        buckets[roles[iid]].append({'item_id': iid, 'span': entry['span']})
        if roles[iid] != entry['kind']:
            changes.append({'item_id': iid, 'old_role': entry['kind'], 'new_role': roles[iid]})
    return QueryFrame(frame.original_text, tuple(buckets['output']), tuple(buckets['modifier'])), changes


def validated_call(call, stage, prompt, payload, validator):
    raw = call(stage, prompt, payload)
    try:
        return validator(raw)
    except (ValueError, TypeError, KeyError) as exc:
        repaired = call(stage + '_contract_retry', prompt,
                        {'input': payload, 'previous_response': raw, 'contract_error': str(exc),
                         'instruction': 'Return the complete corrected JSON object following the stated contract.'})
        return validator(repaired)


def segment(call, text, context, db_name):
    prompt = DISCLOSURE_FRAME_PROMPT.replace('[[DB_name]]', db_name).replace(
        '[[db_schema]]', json.dumps({k: context[k] for k in ('tables', 'foreign_keys')}, ensure_ascii=False)).replace(
        '[[data_description]]', json.dumps(context.get('column_language', {}), ensure_ascii=False)).replace('[[user_request]]', text)
    def check(raw):
        frame = parse_query_frame(raw)
        if frame.original_text != text:
            raise ValueError('Segmentation changed original text')
        return frame
    frame = validated_call(call, 'segmentation', prompt, None, check)
    frame, changes = validated_call(call, 'roles', ROLE_PROMPT,
        {'frame': frame.to_json(), 'required_ids': [i['item_id'] for i in frame.outputs + frame.modifiers]},
        lambda raw: apply_roles(frame, raw))
    return frame, {'role_changes': changes, 'inventory': inventory(frame)}


def relations(frame, raw):
    ids = {x['item_id'] for x in frame.outputs + frame.modifiers}
    if not isinstance(raw, dict) or set(raw) != {'edges'} or not isinstance(raw['edges'], list):
        raise ValueError('Expected edges array')
    seen = set()
    result = []
    spans = {x['item_id']: x['span'] for x in frame.outputs + frame.modifiers}
    for edge in raw['edges']:
        if not isinstance(edge, dict) or set(edge) not in ({'source_id', 'target_id', 'kind'}, {'source_id', 'target_id', 'kind', 'evidence'}):
            raise ValueError('Invalid relation keys')
        a, b = edge['source_id'], edge['target_id']
        if not isinstance(a, str) or not isinstance(b, str) or a not in ids or b not in ids or a == b:
            raise ValueError('Invalid relation endpoints')
        if edge['kind'] not in ('context', 'applies_to'):
            raise ValueError('Invalid relation kind')
        signature = (a, b, edge['kind'])
        if signature in seen:
            raise ValueError('Duplicate edge')
        seen.add(signature)
        if 'evidence' in edge and (not isinstance(edge['evidence'], str) or not edge['evidence'] or edge['evidence'] not in frame.original_text):
            raise ValueError('Relation evidence must quote the original')
        result.append(dict(edge) if 'evidence' in edge else {**edge,
            'source_quote': spans[a], 'target_quote': spans[b]})
    # Cyclic annotations are retained as uncertain dependencies, never traversed
    # without a visited set or silently treated as a topological ordering.
    return {'edges': result}


def indexes(context):
    tables = {t['name']: t for t in context['tables']}
    columns = {t['name'] + '.' + c['name']: t['name'] for t in context['tables'] for c in t['columns']}
    folded = {c.casefold(): c for c in columns}
    if len(folded) != len(columns):
        raise ValueError('Case-insensitive column collision')
    return tables, columns, folded


def grounding(frame, raw, context, target_ids=None):
    ids = {x['item_id'] for x in frame.outputs + frame.modifiers} if target_ids is None else set(target_ids)
    tables, columns, folded = indexes(context)
    table_folded = {t.casefold(): t for t in tables}
    if not isinstance(raw, dict) or set(raw) != {'items'} or not isinstance(raw['items'], list):
        raise ValueError('Expected items array')
    result, seen = [], set()
    for row in raw['items']:
        required = {'item_id', 'kind', 'status', 'requirement', 'scope_tables', 'bindings', 'missing', 'reason'}
        optional = {'question_focus'}
        if (not isinstance(row, dict)
                or not required <= set(row)
                or not set(row) <= required | optional):
            raise ValueError('Invalid grounding fields')
        iid = row['item_id']
        if not isinstance(iid, str) or iid not in ids or iid in seen:
            raise ValueError('Grounding must cover requested IDs exactly once')
        seen.add(iid)
        if row['kind'] not in ('attribute', 'calculation', 'relation', 'instruction') or row['status'] not in ('clear', 'gap', 'unresolved'):
            raise ValueError('Invalid grounding kind/status')
        if any(not isinstance(row[k], str) or not row[k].strip() for k in ('requirement', 'reason')):
            raise ValueError('Missing grounding explanation')
        focus = row.get('question_focus', {})
        if (not isinstance(focus, dict) or set(focus) - set(row['missing'])
                or any(not isinstance(q, str) or not q.strip() for q in focus.values())):
            raise ValueError('question_focus must map missing dimensions to nonempty questions')
        if 'question_focus' in row and set(focus) != set(row['missing']):
            raise ValueError('question_focus must cover each missing dimension')
        for k in ('scope_tables', 'missing'):
            if not isinstance(row[k], list) or any(not isinstance(x, str) for x in row[k]) or len(set(row[k])) != len(row[k]):
                raise ValueError('Invalid grounding list')
        if not set(row['missing']) <= {'scope', 'field', 'operation', 'value'}:
            raise ValueError('Invalid missing decision')
        if (row['status'] == 'gap') != bool(row['missing']):
            raise ValueError('Only gaps have missing decisions')
        if any(t.casefold() not in table_folded for t in row['scope_tables']):
            raise ValueError('Unknown scope table')
        if not isinstance(row['bindings'], list):
            raise ValueError('Bindings must be a list')
        bundles = []
        for bundle in row['bindings']:
            if not isinstance(bundle, list) or not bundle or any(not isinstance(c, str) or c.casefold() not in folded for c in bundle):
                raise ValueError('Invalid binding columns; use only these exact table.column identifiers: ' + ', '.join(columns))
            normalized = sorted({folded[c.casefold()] for c in bundle})
            if normalized not in bundles:
                bundles.append(normalized)
        if len(bundles) > 1:
            raise ValueError('Use one complete dependency bundle, not alternative bundles')
        # A clear scoped entity may have no selected return field yet. The
        # runner treats it as a candidate-selection state, not a linguistic gap.
        result.append({**row, **({'question_focus': dict(focus)} if 'question_focus' in row else {}), 'bindings': bundles,
                       'scope_tables': [table_folded[t.casefold()] for t in row['scope_tables']]})
    if seen != ids:
        raise ValueError('Incomplete grounding coverage')
    return {'items': result}


def interpretation(frame, raw, context, target_ids=None):
    """One model description; state and the dependency bundle are runner-owned."""
    if not isinstance(raw, dict) or set(raw) != {'items'} or not isinstance(raw['items'], list):
        raise ValueError('Expected items array')
    rows = []
    for item in raw['items']:
        keys = {'item_id', 'kind', 'requirement', 'scope_tables', 'required_columns',
                'missing', 'grounded', 'reason'}
        optional = {'question_focus'}
        if (not isinstance(item, dict) or not keys <= set(item)
                or not set(item) <= keys | optional or type(item['grounded']) is not bool):
            raise ValueError('Expected interpretation fields, grounded boolean, and flat required_columns')
        columns = item['required_columns']
        if not isinstance(columns, list) or any(not isinstance(c, str) for c in columns):
            raise ValueError('required_columns must be a flat list of schema columns')
        if not item['grounded'] and columns:
            raise ValueError('Ungrounded interpretation must not declare complete dependencies')
        status = ('gap' if item['missing'] else 'clear' if item['grounded'] else 'unresolved')
        row = {k: v for k, v in item.items() if k not in ('grounded', 'required_columns')}
        rows.append({**row, 'status': status, 'bindings': [columns] if columns else []})
    return grounding(frame, {'items': rows}, context, target_ids)


def current_frame(frame, answers):
    """Project complete replacements without presenting superseded prose as active."""
    projected = frame.to_json()
    replaced = []
    for item in projected['outputs'] + projected['modifiers']:
        answer = (answers or {}).get(item['item_id'], {})
        if answer.get('complete_requirement'):
            item['span'] = answer['complete_requirement']
        elif answer.get('columns') and answer.get('business_meanings'):
            item['span'] = 'Return ' + '; '.join(answer['business_meanings'])
        else:
            continue
        replaced.append(item['item_id'])
    if replaced:
        # An original sentence mixes active and superseded decisions. Relations
        # preserve context between IDs; raw wording stays in the audit frame.
        projected.pop('original_text', None)
        projected['gaps'] = [g for g in projected['gaps'] if g['item_id'] not in replaced]
        projected['replaced_item_ids'] = replaced
    return projected


def owned_requirement(item, answer):
    """Freeze public evidence owned by one item, never model-expanded context."""
    parts = [item['span']]
    if answer.get('operand_columns'):
        labels = answer.get('business_meanings') or answer['operand_columns']
        parts.append('Selected operand: ' + '; '.join(labels))
    for exchange in answer.get('exchanges', []):
        # Whole rendered questions contain the historical request. Only the
        # answer and decision dimension belong in this local contribution.
        focus = exchange.get('question_focus') or exchange['dimension']
        parts.append('Answer to ' + focus + ': ' + exchange['answer'])
    if not answer.get('exchanges'):
        parts.extend('Confirmed: ' + text for text in answer.get('text', []))
    return '\n'.join(parts)


def semantic_answers(answers):
    """Keep decisions, not the historical full request embedded in questions."""
    from copy import deepcopy
    result = deepcopy(answers or {})
    for answer in result.values():
        for exchange in answer.get('exchanges', []):
            exchange['question'] = {'question': exchange.get('question_focus', '')}
            # The asked dimension describes the question, not the answer's extent.
            exchange['asked_dimension'] = exchange.pop('dimension')
    return result


def analyze(call, frame, edges, context, db_name, answers=None, target_ids=None, previous=None):
    ids = target_ids if target_ids is not None else [x['item_id'] for x in frame.outputs + frame.modifiers]
    # Permission decisions and interaction states are not linguistic evidence.
    semantic_keys = ('item_id', 'kind', 'requirement', 'scope_tables', 'bindings', 'missing')
    prior = {iid: {k: row[k] for k in semantic_keys} for iid, row in (previous or {}).items()}
    active_frame = current_frame(frame, answers)
    active_edges = edges
    if active_frame.get('replaced_item_ids'):
        # Edge evidence quotes the historical sentence and can resurrect its
        # replaced conditions. Keep only the relation identity in this view.
        active_edges = {'edges': [{k: e[k] for k in ('source_id', 'target_id', 'kind')}
                                  for e in edges['edges']]}
    payload = {'frame': active_frame, 'relations': active_edges,
               'confirmed_answers': semantic_answers(answers), 'requested_item_ids': ids,
               'previous_items': prior,
               'update_only': previous is not None}
    prompt = GROUNDING.replace('[[DB_name]]', db_name).replace('[[db_schema]]', json.dumps(
        {k: context[k] for k in ('tables', 'foreign_keys')}, ensure_ascii=False)).replace(
        '[[data_description]]', json.dumps(context.get('column_language', {}), ensure_ascii=False)).replace(
        '[[payload]]', json.dumps(payload, ensure_ascii=False))
    def check(raw):
        from copy import deepcopy
        raw = deepcopy(raw)
        active_items = {x['item_id']: x for x in active_frame['outputs'] + active_frame['modifiers']}
        # Public wording belongs to the runner. An update need not regenerate it.
        if isinstance(raw, dict) and isinstance(raw.get('items'), list):
            for row in raw['items']:
                if isinstance(row, dict) and row.get('item_id') in active_items:
                    iid = row['item_id']
                    old = (previous or {}).get(iid, {})
                    row['requirement'] = (old['requirement'] if old.get('status') == 'known'
                        else owned_requirement(active_items[iid], (answers or {}).get(iid, {})))
        return interpretation(frame, raw, context, ids)
    return validated_call(call, 'grounding', prompt, None, check)

