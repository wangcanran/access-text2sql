"""Deterministic, source-scoped JSON candidate shortlist prototype.

This is a candidate-generation aid, not an authorization decision. The normal
menu permission and connectivity gates remain responsible for publication.
"""
import re

from .attribute_candidates import attribute_catalog


QUANTITY_WORDS = {'amount', 'quantity', 'volume', 'emission', 'emissions',
                  'released', 'release', 'mass'}
SCORE_WORDS = {'score', 'rating', 'efficiency', 'rank', 'ranking'}
GROUP_WORDS = {'group', 'groups', 'grouping', 'segment', 'segmented',
               'category', 'categories', 'breakdown'}
GENERIC_TERMS = {'the', 'a', 'an', 'of', 'to', 'in', 'on', 'by', 'for', 'and',
                 'be', 'is', 'are', 'return', 'returned', 'output', 'amount',
                 'quantity', 'measure', 'metric', 'environmental', 'environment',
                 'impact', 'context', 'restricted', 'within', 'groups', 'group',
                 'grouping', 'segment', 'segmented', 'category', 'breakdown',
                 'should', 'which', 'what', 'use', 'used', 'result', 'results',
                 'disposal'}
# Small, auditable domain lexicon used only to retrieve documented schema
# attributes. Extend it only with reviewed benchmark-domain terminology.
CONCEPT_ALIASES = {
    'environmental': {'carbon', 'footprint'},
    'environment': {'carbon', 'footprint'},
    'impact': {'carbon', 'footprint'},
    'measure': {'carbon', 'footprint'},
    'released': {'carbon', 'footprint'},
    'emission': {'carbon', 'footprint'},
    'emissions': {'carbon', 'footprint'},
}
TOKEN = re.compile(r'[a-z0-9]+')


def _terms(text):
    return set(TOKEN.findall((text or '').casefold()))


def _label(path, meaning):
    if path:
        return ' '.join(part.replace('_', ' ') for part in path[1:]).title()
    return re.sub(r'^[A-Za-z0-9_]+(?:\([^)]*\))?\.\s*', '', meaning).strip() or 'Attribute'


def _allowed(row, allowed_columns):
    return allowed_columns is None or row['column'].casefold() in allowed_columns


def schema_scoped_candidates(payload, purpose, *, allowed_columns=None):
    """Return only documented attributes from the current source's JSON column.

    `allowed_columns` must be supplied when publishing results to a requester.
    If the source has no applicable documented JSON leaves, return [] so the
    caller can use the existing candidate flow or mark generation unresolved.
    """
    if purpose not in {'measure', 'grouping'}:
        raise ValueError('purpose must be measure or grouping')
    source = payload.get('source', {})
    source_columns = {c.casefold() for c in source.get('columns', [])}
    if not source_columns:
        return []
    request = ' '.join([payload.get('current_request', ''), source.get('text', ''),
                        payload.get('question_focus', '')])
    query_terms = _terms(request)
    catalog = [r for r in attribute_catalog(payload)
               if r['column'].casefold() in source_columns
               and r['json_path'] and _allowed(r, allowed_columns)]
    if purpose == 'measure':
        wants_quantity = bool(query_terms & QUANTITY_WORDS)
        explicitly_wants_score = bool(query_terms & SCORE_WORDS)
        selected = [r for r in catalog if r['family'] == 'number']
        expanded = set(query_terms)
        for term in query_terms:
            expanded.update(CONCEPT_ALIASES.get(term, ()))
        concept_terms = expanded - GENERIC_TERMS - QUANTITY_WORDS - SCORE_WORDS
        if wants_quantity and not explicitly_wants_score:
            selected = [r for r in selected
                        if not (_terms('/'.join(r['json_path'])+' '+r['meaning']) & SCORE_WORDS)]
            # Numeric type is necessary but insufficient. Require a documented
            # concept match after the versioned, auditable domain expansion.
            selected = [r for r in selected
                        if _terms('/'.join(r['json_path'])+' '+r['meaning']) & concept_terms]
        # When the query names an environmental measure, scores are not treated
        # as amounts unless the request explicitly asks for a score/rating.
        return [dict(attribute_id=r['attribute_id'], column=r['column'],
                     json_path=r['json_path'], label=_label(r['json_path'], r['meaning']),
                     reason='documented numeric leaf in the current source JSON column')
                for r in selected]

    if not (query_terms & GROUP_WORDS):
        return []
    # A query that names an explicit JSON context (e.g. disposal) restricts the
    # shortlist to that subtree. We do not guess a subtree from unrelated terms.
    roots = {r['json_path'][0] for r in catalog}
    context_roots = {root for root in roots if root.casefold() in query_terms}
    if not context_roots:
        return []
    selected = [r for r in catalog if r['json_path'][0] in context_roots
                and r['family'] in {'category', 'temporal'}]
    return [dict(attribute_id=r['attribute_id'], column=r['column'],
                 json_path=r['json_path'], label=_label(r['json_path'], r['meaning']),
                 reason='documented categorical leaf in the requested JSON context')
            for r in selected]
