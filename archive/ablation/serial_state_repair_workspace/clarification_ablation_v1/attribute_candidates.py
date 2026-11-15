"""Schema-owned attribute identities with purpose-specific candidate review.

This prototype takes only the public compiler input and schema documentation.
It never consults reference SQL, private requester intent, execution or policy.
"""
from copy import deepcopy
import hashlib
import json
import re

VERSION = 'attribute_candidate_selection_v2_public_context'

SELECT = '''Select ALL plausible schema attributes for ONE existing clarification
question. Return JSON only, exactly:
{"purpose":{"kind":"measure|grouping|attribute","value_type":"number|category|temporal|boolean|any","meaning":"local business slot"},
 "candidates":[{"attribute_id":"supplied ID","label":"business label","reason":"why it fits this question in context"}]}.
Use question_focus in the complete source context and established related facts.
public_request is the original PUBLIC request and public_conversation contains
only previously accepted PUBLIC exchanges. Preserve original qualifiers about
the kind of quantity, entity, operation and relation even if a later source
paraphrase became more generic. A paraphrase must not widen the requested
business quantity. Existing answers constrain already-settled choices but do
not settle other choices without evidence. Never invent a missing qualifier.
The catalog is an exhaustive enumeration of documented attributes, not a list of
answers. Select IDs only; do not invent fields, JSON paths, defaults or answers.
Do not guess what the private user wants: retain all alternatives that genuinely
fit the missing slot. Inspect relevant attributes in the source's local tables
and known containers, including nested JSON leaves. Do not omit a relevant leaf
because another output uses its physical column. Other tables remain eligible
when their business meaning and association fit the actual question.
Measure, grouping key and parameter value have different roles. For a numeric
amount or measurement, do not list category names, methods, identifiers or
ordinal ratings. A numeric type alone is insufficient: a payment amount is not
an alternative to a physical measurement. Conversely a grouping key can be a
category, date, or a numeric attribute when the request supports it. Preserve
the source's business context; generic words like grouping do not justify every
categorical field in the database. Select a whole JSON container only if the
current slot explicitly requests the whole structured record.
Labels must distinguish alternatives using natural business wording; no SQL,
physical qualified column names or raw JSON paths. The code owns identities and
dependencies. Selection grants no permission. Never answer a missing user choice
or invent an extra filter. Resolve only the supplied existing question.
'''

REVIEW = '''Independently review the candidate set for this ONE existing question.
The draft is provisional. Inspect the FULL schema attribute catalog and public
context for BOTH omitted relevant attributes and irrelevant/wrong-type entries.
Return JSON only with the same purpose and candidates format as the draft, plus
"changes":[{"attribute_id":"supplied ID","kind":"add|remove|relabel","reason":"specific context and schema evidence"}].
Return the complete final candidate set, not a patch. Every changed ID must have
a change record; unchanged candidates may remain. You may correct the purpose
classification to match the actual question, but do not alter the question or
answer its unresolved user choice. Numeric attributes must match the requested
business quantity; grouping attributes must match the stated business context.
Distinguish attributes inside one JSON column. Do not mix grouping dimensions
into a numeric measure menu. Do not add all catalog entries just for coverage.
Use only supplied IDs and natural business labels, no invented fields/paths.
An empty final set means this generation could not identify supported choices,
not that access is forbidden. Selection and review grant no permissions.
'''


class CandidateGenerationUnresolved(ValueError):
    pass


def documented_object(description):
    if not isinstance(description, str):
        return None
    starts = [0] if description.lstrip().startswith('{') else []
    starts += [m.end() for m in re.finditer(r' \| ', description)]
    for start in starts:
        try:
            value = json.loads(description[start:].strip())
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def type_family(value):
    value = value.strip().casefold()
    if re.match(r'^(tinyint|smallint|bigint|integer|int\b|real\b|float|double|numeric|decimal|number)', value):
        return 'number'
    if re.match(r'^(timestamp|datetime|date\b|time\b)', value):
        return 'temporal'
    if value.startswith(('boolean', 'bool')):
        return 'boolean'
    if value.startswith(('text', 'varchar', 'char', 'string')) or '_enum' in value.split('.', 1)[0]:
        return 'category'
    if value.startswith(('json', 'object')):
        return 'structured'
    return 'unknown'


def attribute_catalog(payload):
    local = set(payload.get('source', {}).get('tables', []))
    bound = set(payload.get('source', {}).get('columns', []))
    rows = []
    for handle, column in payload['candidate_catalog'].items():
        table, name = column.split('.', 1)
        dtype, description = payload['schema']['tables'][table][name]
        def add(path, meaning, datatype):
            identity = json.dumps([column, path], ensure_ascii=False, separators=(',', ':'))
            rows.append(dict(attribute_id='a_'+hashlib.sha256(identity.encode()).hexdigest()[:20],
                column=column, column_handle=handle, json_path=path, meaning=meaning,
                datatype=datatype, family=type_family(datatype),
                local_table=table in local, known_container=column in bound))
        add([], description or name.replace('_', ' '), dtype)
        doc = documented_object(description) if type_family(dtype) == 'structured' else None
        def visit(obj, prefix):
            for key, value in obj.items():
                path = prefix + [key]
                if isinstance(value, dict):
                    visit(value, path)
                elif isinstance(value, str) and value.strip():
                    add(path, value, value.split('.', 1)[0])
        if doc is not None:
            visit(doc, [])
    if len({r['attribute_id'] for r in rows}) != len(rows):
        raise ValueError('Attribute catalog has duplicate identities')
    return sorted(rows, key=lambda x: (not x['known_container'], not x['local_table'], x['attribute_id']))


def public_input(payload, catalog):
    # Explicit allowlist prevents accidentally passing private fixture fields.
    result = {k: deepcopy(payload[k]) for k in ['public_request', 'public_conversation', 'current_request', 'source', 'question_focus',
              'read_only_context', 'pending_decisions'] if k in payload}
    result['attribute_catalog'] = deepcopy(catalog)
    return result


def validate_selection(raw, catalog, *, review=False, draft=None):
    allowed = {'purpose', 'candidates', 'changes'} if review else {'purpose', 'candidates'}
    if not isinstance(raw, dict) or set(raw) != allowed:
        raise ValueError('Return exactly purpose, candidates' + (' and changes' if review else ''))
    purpose = raw['purpose']
    if (not isinstance(purpose, dict) or set(purpose) != {'kind', 'value_type', 'meaning'}
            or purpose['kind'] not in {'measure', 'grouping', 'attribute'}
            or purpose['value_type'] not in {'number', 'category', 'temporal', 'boolean', 'any'}
            or not isinstance(purpose['meaning'], str) or not purpose['meaning'].strip()):
        raise ValueError('Invalid local purpose contract')
    if not isinstance(raw['candidates'], list):
        raise ValueError('Candidates must be an array')
    by_id = {r['attribute_id']: r for r in catalog}
    seen, labels = set(), set()
    for row in raw['candidates']:
        if (not isinstance(row, dict) or set(row) != {'attribute_id', 'label', 'reason'}
                or not isinstance(row['attribute_id'], str) or row['attribute_id'] not in by_id
                or row['attribute_id'] in seen
                or any(not isinstance(row[k], str) or not row[k].strip() for k in ['label', 'reason'])
                or row['label'].strip().casefold() in labels):
            raise ValueError('Candidates require unique known IDs, distinct labels and evidence')
        seen.add(row['attribute_id']); labels.add(row['label'].strip().casefold())
        attr = by_id[row['attribute_id']]
        # A documented enum/JSON container cannot become a numeric measurement.
        if purpose['value_type'] == 'number' and attr['family'] not in {'number', 'unknown'}:
            raise ValueError('Non-numeric attribute in numeric slot: '+row['attribute_id'])
        if attr['column'] in row['label'] or '->' in row['label']:
            raise ValueError('Public labels must not expose physical SQL references')
    if review:
        if not isinstance(raw['changes'], list):
            raise ValueError('Changes must be an array')
        old = {r['attribute_id']: r for r in draft['candidates']}
        new = {r['attribute_id']: r for r in raw['candidates']}
        expected = {(i, 'add') for i in new.keys()-old.keys()} | {(i, 'remove') for i in old.keys()-new.keys()}
        expected |= {(i, 'relabel') for i in new.keys() & old.keys() if new[i]['label'] != old[i]['label']}
        changes = set()
        for change in raw['changes']:
            if (not isinstance(change, dict) or set(change) != {'attribute_id', 'kind', 'reason'}
                    or change['attribute_id'] not in by_id or change['kind'] not in {'add','remove','relabel'}
                    or not isinstance(change['reason'], str) or not change['reason'].strip()):
                raise ValueError('Each review change needs a known ID, kind and evidence')
            changes.add((change['attribute_id'], change['kind']))
        if changes != expected:
            raise ValueError('Review changes must account for exactly the actual additions/removals/labels')
    return deepcopy(raw)


def bounded_call(call, stage, prompt, payload, validator):
    original = deepcopy(payload)
    for attempt in range(2):
        data = original if not attempt else dict(input=original, previous_response=raw, contract_error=str(error))
        raw = call(stage if not attempt else stage+'_contract_retry', prompt, deepcopy(data))
        try:
            return validator(raw)
        except (ValueError, TypeError, KeyError) as exc:
            error = exc
    raise CandidateGenerationUnresolved(stage+': '+str(error))


def generate_candidates(call, payload):
    catalog = attribute_catalog(payload)
    evidence = public_input(payload, catalog)
    draft = bounded_call(call, 'attribute_candidate_select', SELECT, evidence,
                         lambda raw: validate_selection(raw, catalog))
    review = bounded_call(call, 'attribute_candidate_review', REVIEW+'\n'+SELECT[SELECT.index('Use question_focus'):],
                          dict(evidence, draft=draft),
                          lambda raw: validate_selection(raw, catalog, review=True, draft=draft))
    if not review['candidates']:
        raise CandidateGenerationUnresolved('No supported attribute choices; not an access denial')
    by_id = {r['attribute_id']: r for r in catalog}
    final = [dict(c, attribute=deepcopy(by_id[c['attribute_id']])) for c in review['candidates']]
    return dict(version=VERSION, catalog=catalog, draft=draft, review=review, candidates=final)
