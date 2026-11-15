"""Experimental migration of historical access evidence, never access grants.

Semantic lineage remains model-owned. Code checks complete coverage, explicit
revision witnesses, current IDs, and re-derives denials from retained columns.
"""
from copy import deepcopy
import hashlib
import json

from .runtime import engine_module as frozen_module

PROMPT = '''You are tracing dependency evidence across revisions of a database request.
current_request and current_nodes describe the active request. obligations contain
previously detected restricted dependencies whose owners no longer align with
that analysis. Schema mapping can omit a dependency; absence alone never retires it.
Review EACH obligation independently, using the supplied real decisions and
protocol removals. Old node IDs and missing slots are historical, not current tasks.

Return one row per obligation:
- carry: its contribution survives; give all current node IDs that require this
  dependency. Include a dependency omitted by the new mapper if still necessary.
  This transfers evidence; it does not choose a field for the user.
- retire: an explicit removal withdrew this contribution, or an accepted choice
  superseded the scope/field requiring it. Cite that event's ID and an exact quote
  from its supplied quotes. Explain the semantic connection to THIS dependency.
  A mere rewrite, node disappearance, or missing mapping is not sufficient.
- unresolved: neither retirement nor a current owner is supported. Do not guess.

A column can serve multiple contributions. Removing a returned salary does not
remove a surviving salary filter. Removing birthday output does not remove birth
date needed for age. A shared connection remains required wherever a surviving
requirement needs it. Conversely, an accepted account population need not retain
the join to an earlier loan-specific population that it replaced. Do not turn a
clear accepted population back into an unanswered scope merely because old
evidence still has missing slots. Later events take precedence over earlier ones;
removal.after_turn gives its position among actual accepted turns.

Do not change the request, select fields, grant permissions, or generate questions.
Only JSON: {"effects":[{"obligation_id":"...","action":"carry|retire|unresolved",
"current_ids":[],"event_id":"","quote":"","reason":"..."}]}.
carry needs nonempty current_ids and empty event_id/quote. retire needs empty
current_ids and a witnessed event. unresolved needs all three empty plus a reason.
'''


class PermissionLineageUnresolved(ValueError):
    """Legacy exception retained for callers that explicitly require a resolved ledger."""


def revision_events(session):
    events = []
    for i, turn in enumerate(session.get('turns', []), 1):
        if turn['answer']['action'] not in ('choose', 'answer'):
            continue
        quotes = [o['text'] for o in turn.get('selected_options', []) if o.get('text')]
        if turn['answer'].get('text'):
            quotes.append(turn['answer']['text'])
        if quotes:
            events.append({'event_id': f'turn:{i}', 'kind': 'accepted_answer',
                'after_turn': i, 'target_fragment': turn.get('target_fragment'),
                'asked_dimension': turn.get('asked_dimension'),
                'selection_mode': turn.get('selection_mode'), 'quotes': quotes})
    for i, removal in enumerate(session.get('reduction_audit', []), 1):
        quotes = [removal[k] for k in ('previous_request', 'request')
                  if isinstance(removal.get(k), str) and removal[k]]
        events.append({'event_id': f'removal:{i}', 'kind': 'protocol_removal',
            'after_turn': removal.get('after_turn', 0),
            'target_fragment': removal.get('target_fragment'),
            'question': removal.get('question'),
            'previous_request': removal.get('previous_request'),
            'request': removal.get('request'), 'quotes': quotes})
    return sorted(events, key=lambda e: (e['after_turn'], e['kind'] == 'protocol_removal'))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def evidence_sources(session):
    """The same evidence collection serves migration, deletion and audit."""
    sources = deepcopy(session.get('permission_issues', {}))
    for key, pending in session.get('lineage_pending', {}).items():
        if key in sources:
            raise ValueError('Historical evidence key collides with a current issue')
        sources[key] = deepcopy(pending['issue'])
    return sources


def evidence_entries(session):
    """One stable identity per source contribution and restricted column.

    Model-facing eN IDs are local to a review, never ledger keys. Narrowing each
    stored issue to ONE column prevents partial retirement from restoring others.
    Legacy pending records are normalized on read, including repeated copies.
    """
    entries = {}
    for key, original in evidence_sources(session).items():
        issue = deepcopy(original)
        issue['inventory_id'] = issue.get('inventory_id', issue.get('source_inventory'))
        for column in sorted(set(issue['denied_columns'])):
            identity = [issue.get('inventory_id'), issue['item_id'], issue['role'],
                        issue['span'], issue['requirement'], column]
            eid = 'lineage:' + digest(identity)
            if eid in entries:
                entries[eid]['source_keys'].append(key)
                continue
            source = {**issue, 'required_columns': [column], 'denied_columns': [column],
                      'bindings': [[column]]}
            entries[eid] = {'evidence_id': eid, 'source_keys': [key],
                            'column': column, 'issue': source}
    return entries


def review_input(session, context):
    view = session['view']
    current = view['items']
    obligations, direct = [], []
    for eid, entry in evidence_entries(session).items():
        issue, column = entry['issue'], entry['column']
        node = current.get(issue['item_id'])
        common = {'evidence_id': eid, 'issue_key': entry['source_keys'][0], 'column': column}
        # Only qualified identity AND a currently declared dependency make
        # migration mechanical. A reused ID in a different graph is not proof.
        if (node is not None and issue.get('inventory_id') == view['inventory_id']
                and column in node['required_columns']):
            direct.append({**common, 'current_ids': [node['item_id']]})
        else:
            obligations.append({**common, 'obligation_id': 'e' + str(len(obligations) + 1),
                'source_inventory': issue.get('inventory_id'),
                'source_id': issue['item_id'], 'role': issue['role'],
                'span': issue['span'], 'requirement': issue['requirement'],
                'meaning': context.get('column_language', {}).get(column, '')})
    payload = {'current_request': session['current_request'], 'inventory_id': view['inventory_id'],
        'current_nodes': [{k: deepcopy(n[k]) for k in ('item_id', 'span', 'role',
            'requirement', 'required_columns', 'scope_tables', 'missing')}
            for n in current.values()], 'relations': deepcopy(view['relations']),
        'obligations': obligations, 'events': revision_events(session)}
    return payload, direct


def validate_effects(raw, payload):
    if not isinstance(raw, dict) or set(raw) != {'effects'} or not isinstance(raw['effects'], list):
        raise ValueError('Expected effects array')
    expected = {o['obligation_id']: o for o in payload['obligations']}
    if len(expected) != len(payload['obligations']):
        raise ValueError('Review input has duplicate obligation IDs')
    ids = {n['item_id'] for n in payload['current_nodes']}
    events = {e['event_id']: e for e in payload['events']}
    seen = set()
    for row in raw['effects']:
        if (not isinstance(row, dict) or set(row) != {'obligation_id', 'action', 'current_ids',
                                                     'event_id', 'quote', 'reason'}):
            raise ValueError('Expected one documented dependency effect')
        oid = row['obligation_id']
        if not isinstance(oid, str) or oid not in expected or oid in seen:
            raise ValueError('Unknown or duplicate obligation')
        seen.add(oid)
        if (row['action'] not in ('carry', 'retire', 'unresolved')
                or not isinstance(row['current_ids'], list)
                or any(not isinstance(i, str) or i not in ids for i in row['current_ids'])
                or len(set(row['current_ids'])) != len(row['current_ids'])
                or any(not isinstance(row[k], str) for k in ('event_id', 'quote', 'reason'))
                or not row['reason'].strip()):
            raise ValueError('Invalid dependency effect')
        if row['action'] == 'carry':
            if not row['current_ids'] or row['event_id'] or row['quote']:
                raise ValueError('Carry needs current owners, no retirement witness')
        elif row['action'] == 'retire':
            event = events.get(row['event_id'])
            if (row['current_ids'] or event is None or not row['quote'].strip()
                    or not any(row['quote'] in q for q in event['quotes'])):
                raise ValueError('Retirement requires an exact explicit revision witness')
        elif row['current_ids'] or row['event_id'] or row['quote']:
            raise ValueError('Unresolved cannot grant retirement or invent ownership')
    if seen != set(expected):
        raise ValueError('Every historical dependency must be covered exactly once')
    return deepcopy(raw['effects'])


def apply_effects(session, context, payload, effects, direct):
    """Atomic commit: current dependencies always undergo code permission checks."""
    expected, expected_direct = review_input(session, context)
    if payload != expected or direct != expected_direct:
        raise ValueError('Stale or incomplete dependency review input')
    effects = validate_effects({'effects': effects}, payload)
    new = deepcopy(session)
    entries = evidence_entries(session)
    obligations = {o['obligation_id']: o for o in payload['obligations']}
    unresolved = [e for e in effects if e['action'] == 'unresolved']
    pending = {}
    previous = evidence_sources(session)
    # Rebuild the ledger from a complete review, not in-place pops by temporary
    # IDs. Unresolved evidence blocks delivery, not existing business questions.
    for effect in unresolved:
        obligation = obligations[effect['obligation_id']]
        eid = obligation['evidence_id']
        pending[eid] = {'evidence_id': eid, 'issue': deepcopy(entries[eid]['issue']),
            'reason': effect['reason'], 'status': 'unresolved'}
    assignments = direct + [{'column': obligations[e['obligation_id']]['column'],
                              'current_ids': e['current_ids']} for e in effects if e['action'] == 'carry']
    for assignment in assignments:
        for iid in assignment['current_ids']:
            node = new['view']['items'][iid]
            deps = set(node['required_columns']) | {assignment['column']}
            node['required_columns'] = sorted(deps)
            node['bindings'] = [sorted(deps)]
    for node in new['view']['items'].values():
        node['denied_columns'] = sorted(set(node['required_columns']) - set(new['allowed']))
    # Every source column is now carried, retired, or pending. Freshly detected
    # dependencies are independently checked, including columns just retired
    # for an OLD contribution that another current contribution still needs.
    new['permission_issues'] = {}
    if pending:
        new['lineage_pending'] = pending
    else:
        new.pop('lineage_pending', None)
    new.setdefault('permission_lineage_audit', []).append({
        'inventory_id': new['view']['inventory_id'], 'previous_issues': previous,
        'obligations': deepcopy(payload['obligations']), 'events': deepcopy(payload['events']),
        'effects': effects, 'mechanical_owners': direct,
        'unresolved_pending': deepcopy(unresolved)})
    result = frozen_module('session_update').finish_view(new, context)
    result['lineage_review_signature'] = review_signature(result, context)
    return result


def review_signature(session, context):
    payload, _ = review_input(session, context)
    # Source storage keys and temporary eN IDs can change without new evidence.
    payload['obligations'] = sorted(
        [{k: v for k, v in o.items() if k not in ('issue_key', 'obligation_id')}
         for o in payload['obligations']], key=lambda o: o['evidence_id'])
    return digest([PROMPT, payload, sorted(session['allowed'])])


def reconcile(call, session, context):
    if not session.get('view') or not (session.get('permission_issues') or session.get('lineage_pending')):
        return session
    payload, direct = review_input(session, context)
    if session.get('lineage_review_signature') == review_signature(session, context):
        return session
    if not payload['obligations']:
        stale = bool(session.get('lineage_pending')) or any(
                    any(issue.get(k) != session['view']['items'][issue['item_id']].get(k)
                        for k in ('missing', 'question_focus', 'scope_tables', 'span', 'requirement'))
                    for issue in session['permission_issues'].values())
        if stale:
            return apply_effects(session, context, payload, [], direct)
        return session
    effects = frozen_module('analysis').validated_call(call, 'permission_lineage', PROMPT,
        payload, lambda raw: validate_effects(raw, payload))
    return apply_effects(session, context, payload, effects, direct)
