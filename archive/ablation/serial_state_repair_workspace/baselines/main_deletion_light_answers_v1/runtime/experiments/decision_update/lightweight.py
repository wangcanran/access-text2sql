"""Opt-in semantic transactions. No source-quote contract or per-turn prose rebuild.

Only the model's local semantics are probabilistic. IDs, untouched-node retention,
permission evidence, routing invalidation and the atomic commit are code-owned.
"""
from contextlib import contextmanager
from copy import deepcopy
import re
from unittest.mock import patch

from ... import method as main
from ...engine import analysis, session_update, simple_session, hierarchical_questions
from ...model import QueryFrame
from ...objective_contract import has_query_objective
from .ledger import pending, discover
from .question_delta import apply_questions, asked_ids
from .light_prompt import UPDATE, RENDER, DECISION


def schema_payload(context):
    # Keep every schema field/meaning. Remove only duplicate placement and FK
    # extraction metadata; never truncate the domain to a convenient menu size.
    meanings = context.get('column_language', {})
    return {'tables': {t['name']: {c['name']: [c['type'], meanings.get(t['name'] + '.' + c['name'], '')]
                                 for c in t['columns']} for t in context['tables']},
            'foreign_keys': [{k: e[k] for k in ('from_table', 'from_column', 'to_table', 'to_column')}
                             for e in context['foreign_keys']]}


def node_payload(n):
    return {'id': n['item_id'], 'text': n['requirement'], 'role': n['role'], 'kind': n['kind'],
            'tables': n['scope_tables'], 'columns': n['required_columns'],
            'questions': n['question_focus']}


def answered_dimensions(event):
    """Close the originating decision, even when its menu narrows to fields.

    A scope task rendered as a field menu does not answer independent field
    tasks in the same compound node. This provenance is runner-owned.
    """
    turn = event.get('turn', {})
    origin = turn.get('scoped_permission_decision', {}).get('origin_dimension')
    if origin in ('scope', 'field', 'permission'):
        return {origin}
    return (set(turn.get('requested_dimensions', [])) | {event['dimension']}
            | {s.rsplit('/', 1)[-1] for s in turn.get('requested_slots', [])})


def event_payload(event):
    turn = event.get('turn', {})
    decision = event.get('decision') or turn.get('scoped_permission_decision') or event.get('purpose') or {}
    return {'kind': event['kind'], 'target': event['target'],
            'dimension': event['dimension'], 'question': event['question'],
            'resolved_dimensions': sorted(answered_dimensions(event)) if event['kind'] == 'answer' else [],
            'purpose': {k: decision[k] for k in ('requirement', 'role', 'kind',
                'remaining_requirements') if k in decision},
            'answer': turn.get('answer', {}),
            'selected': [{k: o[k] for k in ('text', 'required_columns') if k in o}
                         for o in turn.get('selected_options', [])]}


def region(view, event):
    ids = set(session_update.affected_region(view, event['target']))
    if event['kind'] == 'removal':
        # A deletion may orphan an upstream qualification or a downstream
        # reference. Review its connected component; do not delete by reachability.
        changed = True
        while changed:
            before = len(ids)
            for e in view['relations']['edges']:
                pair = {e['source_id'], e['target_id']}
                if ids & pair:
                    ids |= pair
            changed = len(ids) != before
    return ids


def projection(items, edges):
    """Internal, role-explicit active text; not a claimed verbatim user sentence."""
    lines = [f"{i} ({n['role']}): {n['requirement']}" for i, n in items.items()]
    lines += [f"{e['source_id']} {e['kind']} {e['target_id']}" for e in edges]
    return '\n'.join(lines)


def seed_range_choices(result):
    """Narrow offline guard: partitive 'some of' is unresolved coverage.

    Does NOT label every 'some' as a gap (existential 'some customer' is valid).
    Never invent a number. General ambiguity detection remains model-owned.
    """
    info = result[4]['progressive']
    choices = info.setdefault('source_open_choices', {})
    for row in result[2]['items']:
        span = result[1][row['item_id']]['span']
        if re.search(r'\bsome\s+of\b', span, re.I) and 'value' not in row['missing']:
            q = 'Which subset or coverage is intended for this part?'
            row['missing'].append('value')
            row['question_focus']['value'] = q
            row['status'] = 'gap'
            choices.setdefault(row['item_id'], []).append({'dimension': 'value', 'span': span, 'question': q})
            info.setdefault('offline_guards', []).append({'item_id': row['item_id'], 'rule': 'partitive_some_of'})
    return result


def objective_kind(role, kind, columns, questions):
    # A profile with actual attributes or pending choices is a query objective,
    # even when the model calls the compound container an instruction. Pure
    # context/headings without either are still not returned contributions.
    return 'attribute' if role == 'output' and kind == 'instruction' and (columns or questions) else kind


def retain_unspecified_output(row, context):
    """Narrow offline guard: generic returned information is not a chosen field.

    Join keys establish subjects, not what information the user wants returned.
    Explicit names/identifiers/calculations and non-key dependencies are outside
    this guard. It adds a question, never infers a field or changes a role.
    """
    generic = re.search(
        r'\b(?:return|show|provide|list|display|state)\s+'
        r'(?:(?:the|some|related|relevant|associated|available)\s+)*'
        r'(?:information|details|data|profile)\b', row['requirement'], re.I)
    keys = {e[side + '_table'] + '.' + e[side + '_column']
            for e in context['foreign_keys'] for side in ('from', 'to')}
    if (row['role'] == 'output' and generic and 'field' not in row['missing']
            and set(row['required_columns']) <= keys):
        row['missing'].append('field')
        row['question_focus']['field'] = 'Which information should be returned for this part?'
        row['grounded'] = False
        return True
    return False


def shared_review_ids(base, dropped):
    return {e['source_id'] for e in base['relations']['edges']
            if e['target_id'] in dropped and e['source_id'] not in dropped
            and base['items'][e['source_id']]['role'] == 'modifier' and e['kind'] == 'applies_to'}


def protected_outputs(base, event):
    """Withdrawal authority belongs to a purpose, not its compound container.

    No text matching or semantic repair: a predicate withdrawal cannot authorize
    erasing returned contributions. Only the asked output may be withdrawn.
    """
    decision = (event.get('decision') or event.get('turn', {}).get('scoped_permission_decision')
                or event.get('purpose') or {})
    return {i for i, n in base['items'].items() if n['role'] == 'output'
            and not (event['kind'] == 'removal' and i == event['target']
                     and decision.get('role', n['role']) == 'output')}


def normalize_transaction(raw):
    """Normalize unambiguous envelope placement, without changing semantics."""
    raw = deepcopy(raw)
    if isinstance(raw, dict) and isinstance(raw.get('updates'), dict):
        for key in ('edges', 'retire_columns'):
            if key in raw['updates']:
                value = raw['updates'].pop(key)
                if key in raw and raw[key] != value:
                    raise ValueError('Conflicting transaction ' + key)
                raw[key] = value
    return raw


def reuse_field_labels(fallback, call, session, q, frontier, context):
    """Reuse model-written compiler labels, never substitute raw schema names."""
    labels = {r['column']: r['label'].strip() for r in q['item'].get('field_candidates', [])
              if isinstance(r.get('label'), str) and r['label'].strip()}
    leaves = {o['choice_id']: o for o in frontier['leaves']}
    result = {}
    for cid in frontier['active']:
        columns = leaves[cid]['required_columns']
        if len(columns) != 1 or columns[0] not in labels or labels[columns[0]] == columns[0]:
            return fallback(call, session, q, frontier, context)
        result[cid] = labels[columns[0]]
    if len({s.casefold() for s in result.values()}) != len(result):
        return fallback(call, session, q, frontier, context)
    return result


def normalize_scope_choices(value, context):
    """Optional UI metadata cannot invalidate a valid semantic decision.

    Omitted/malformed metadata uses the existing renderer. Canonicalize schema
    IDs and duplicate options here; never infer a label or a dependency.
    """
    if not isinstance(value, list):
        return None
    columns = {t['name'] + '.' + c['name'] for t in context['tables'] for c in t['columns']}
    canonical = {c.casefold(): c for c in columns}
    result, labels = [], {}
    for row in value:
        if (not isinstance(row, dict) or not isinstance(row.get('text'), str)
                or not row['text'].strip() or not isinstance(row.get('required_columns'), list)
                or not row['required_columns'] or any(not isinstance(c, str) or c.casefold() not in canonical
                                                     for c in row['required_columns'])):
            return None
        label = row['text'].strip()
        dependencies = sorted({canonical[c.casefold()] for c in row['required_columns']})
        marker = label.casefold()
        if marker in labels:
            if labels[marker] != dependencies:
                return None  # Ambiguous business labels need rendering, not guessing.
            continue
        labels[marker] = dependencies
        result.append({'text': label, 'required_columns': dependencies})
    return result


def reuse_scope_choices(fallback, call, session, q, context):
    """Feed compiled business options through the unchanged access checks."""
    choices = q.get('scoped_permission_decision', {}).get('scope_choices')
    if q['dimension'] != 'scope' or choices is None:
        return fallback(call, session, q, context)

    def cached_call(stage, prompt, payload):
        if stage == 'scope_menu_choices':
            return {'options': deepcopy(choices)}
        return call(stage, prompt, payload)

    return fallback(cached_call, session, q, context)


def deletion_patch(base, target, decision):
    """Execute a classified whole-contribution withdrawal without a model call.

    An explicit empty independent remainder is required. Connectivity alone
    never licenses deletion. Only orphaned modifiers whose applies_to targets
    have ALL disappeared are cascaded; shared modifiers survive.
    """
    owner = base['items'][target]
    if decision.get('remaining_requirements') != [] or decision.get('role') != owner['role']:
        return None
    dropped = {target}
    while True:
        before = len(dropped)
        for iid, n in base['items'].items():
            targets = {e['target_id'] for e in base['relations']['edges']
                       if e['source_id'] == iid and e['kind'] == 'applies_to'}
            if n['role'] == 'modifier' and targets and targets <= dropped:
                dropped.add(iid)
        if len(dropped) == before:
            break
    # A surviving shared qualification may contain separate calculations for
    # several outputs. Changing its targets alone cannot update that meaning.
    # Review it in the SAME semantic transaction, never a second repair call.
    if shared_review_ids(base, dropped):
        return None
    return {'updates': {i: [] for i in sorted(dropped)}}


def transaction(session, base, event, raw, context, *, keep_shared=False, editable_ids=None,
                compound_answers=False, question_updater=None):
    """Validate once, then atomically materialize an entirely new session."""
    # Resolve identifier case in code, once, before comparing evidence. This
    # is schema lookup, not a reason to ask the model to repeat its response.
    raw = normalize_transaction(raw)
    canonical = {c.casefold(): c for t in context['tables']
                 for c in (t['name'] + '.' + col['name'] for col in t['columns'])}
    def column(value):
        return canonical.get(value.casefold(), value) if isinstance(value, str) else value
    if not isinstance(raw, dict) or 'updates' not in raw or set(raw) - {'updates', 'edges', 'retire_columns', 'question_changes'}:
        raise ValueError('Expected updates and optional edges/retire_columns/question_changes')
    if event['kind'] != 'answer' and raw.get('question_changes'):
        raise ValueError('Removal questions use the surviving requirement snapshot')
    raw.setdefault('edges', [])
    raw.setdefault('retire_columns', {})
    if raw['retire_columns'] == []:
        raw['retire_columns'] = {}
    updates, retire = raw['updates'], raw['retire_columns']
    editable = region(base, event) if editable_ids is None else set(editable_ids)
    if event['target'] not in editable or not editable <= set(base['items']):
        raise ValueError('Invalid transaction boundary')
    if not isinstance(updates, dict) or not set(updates) <= editable or not isinstance(retire, dict):
        raise ValueError('Updates must reference editable parent IDs')
    if event['target'] not in updates:
        raise ValueError('The event must update its target')
    if not set(retire) <= set(updates):
        raise ValueError('Retirement requires an updated parent')
    dropped = {p for p, rows in updates.items() if rows == []}
    # An explicit surviving attachment also means "keep this modifier". Do
    # not ask the model to recopy unchanged text just to acknowledge review.
    explicit = raw['edges'] if isinstance(raw['edges'], list) else []
    # The ordinary model transaction must explicitly review a shared modifier.
    # The deletion-plan arm can instead ask code to retain its unchanged text
    # and surviving attachments; this is safe only for a classified whole
    # contribution and is never exposed as a model response field.
    kept_shared = set()
    for iid in shared_review_ids(base, dropped):
        targets = {e['target_id'] for e in base['relations']['edges']
                   if e['source_id'] == iid and e['kind'] == 'applies_to'
                   and e['target_id'] not in dropped}
        if keep_shared and targets:
            kept_shared.add(iid)
        elif targets and all([iid, t, 'applies_to'] in explicit for t in targets):
            kept_shared.add(iid)
    reviews = shared_review_ids(base, dropped) - set(updates) - kept_shared
    if reviews:
        raise ValueError('Review shared modifier text/questions in the same update: ' + ', '.join(sorted(reviews)))
    for rows in updates.values():
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    if isinstance(row.get('columns'), list):
                        row['columns'] = list(dict.fromkeys(column(c) for c in row['columns']))
                    if isinstance(row.get('candidates'), dict):
                        row['candidates'] = {column(c): v for c, v in row['candidates'].items()}
    retire = {p: [column(c) for c in cs] if isinstance(cs, list) else cs for p, cs in retire.items()}
    old_pending = pending(base)
    by_decision = {d['decision_id']: d for d in old_pending}
    turn = event.get('turn', {})
    dimensions = answered_dimensions(event)
    info = deepcopy(base.get('progressive', {'parents': {}, 'retired': {}}))
    used = set(base['items']) | set(info['parents']) | set(info['retired'])
    serial = max([int(i[1:]) for i in used if re.fullmatch(r'f\d+', i)] + [0]) + 1
    replacements, local, mapping, choices, carried = {}, {}, [], {}, []
    fields = {'id', 'text', 'role', 'kind', 'tables', 'columns', 'questions', 'candidates', 'pending'}
    for parent, rows in updates.items():
        if not isinstance(rows, list) or (not rows and event['kind'] != 'removal'):
            raise ValueError('Only removal can drop a parent')
        replacements[parent] = []
        for row in rows:
            if not isinstance(row, dict) or {'id', 'text', 'columns'} - set(row) or set(row) - fields:
                raise ValueError('Invalid local node fields')
            row.setdefault('questions', {})
            row.setdefault('kind', base['items'][parent]['kind'])
            row.setdefault('tables', base['items'][parent]['scope_tables'])
            row.setdefault('candidates', {})
            temp = row['id']
            if (not isinstance(temp, str) or not temp.strip() or temp in local
                    or temp in base['items'] and temp != parent):
                raise ValueError('Local node aliases must be unique and not shadow another parent')
            if not isinstance(row['text'], str) or not row['text'].strip():
                raise ValueError('Node text must be nonempty')
            iid = f'f{serial}'
            serial += 1
            local[temp] = iid
            replacements[parent].append(iid)
            info['parents'][iid] = parent
            role = row.get('role', base['items'][parent]['role'])
            # This is a structural constraint, not a text classifier. No retry
            # for model role drift on an existing modifier: inherit its role.
            if base['items'][parent]['role'] == 'modifier':
                role = 'modifier'
            if role not in ('output', 'modifier'):
                raise ValueError('Invalid node role')
            questions = row['questions']
            if not isinstance(questions, dict) or not set(questions) <= {'scope', 'field', 'operation', 'value'}:
                raise ValueError('Invalid question dimensions')
            if not isinstance(row['candidates'], dict):
                raise ValueError('Candidates must map columns to business labels')
            columns = row['columns']
            row['kind'] = objective_kind(role, row['kind'], columns, questions)
            mapping.append({'item_id': iid, 'role': role, 'kind': row['kind'], 'requirement': row['text'].strip(),
                'scope_tables': row['tables'], 'required_columns': columns,
                'grounded': bool(columns) or row['kind'] == 'instruction',
                'missing': list(questions), 'question_focus': deepcopy(questions),
                'field_candidates': [{'column': c, 'label': v} for c, v in row['candidates'].items()],
                'reason': 'Atomic local event update'})
            # Removal keeps its survivor-snapshot policy. Answer questions are
            # inherited by identity below, independently of omitted child hints.
            routed = row.get('pending', [])
            if not isinstance(routed, list) or any(not isinstance(d, str) for d in routed):
                raise ValueError('Pending must contain decision IDs')
            for did in routed:
                if did not in by_decision or by_decision[did]['owner_id'] != parent:
                    raise ValueError('Pending decision must stay in its parent descendants')
                if event['kind'] == 'answer':
                    continue
                dimension = by_decision[did]['dimension']
                if dimension not in questions:
                    continue
                decision = {**deepcopy(by_decision[did]), 'owner_id': iid}
                decision.update(question=questions[dimension], source_span=row['text'].strip())
                carried.append(decision)
    # Deletion operates on surviving semantics; do not restore withdrawn tasks.
    # Answers instead conserve every independent pending decision by default.
    if event['kind'] == 'removal':
        carried.extend(deepcopy(old) for old in old_pending if old['owner_id'] not in updates)
    question_audit = []
    if event['kind'] == 'answer':
        carried, info['next_decision_id'], question_audit = (question_updater or apply_questions)(
            base, event, raw, mapping, replacements, local, dimensions)
    for row in mapping:
        if 'field' not in row['missing']:
            row['field_candidates'] = []  # Unused menu labels are not a semantic contract error.
    # Answers conserve commitments. Removal instead commits the dependencies of
    # the surviving semantics; old-only keys are retired by the code diff. A
    # second retirement annotation must not force deleted joins back into text.
    for parent, children in replacements.items():
        old_cols = set(base['items'][parent]['required_columns'])
        deps = {c for n in mapping if n['item_id'] in children for c in n['required_columns']}
        if event['kind'] == 'removal':
            retire[parent] = sorted(old_cols - deps)
            continue
        withdrawn = retire.get(parent, [])
        if not isinstance(withdrawn, list) or any(not isinstance(c, str) for c in withdrawn):
            raise ValueError('retire_columns values must be column lists')
        withdrawn = set(withdrawn)
        if not withdrawn <= old_cols:
            raise ValueError('Cannot retire a dependency not owned by this parent')
        if withdrawn and event['kind'] != 'removal' and not (parent == event['target'] and event['dimension'] in ('field', 'scope', 'permission')):
            raise ValueError('Only a field/scope replacement or removal can retire dependencies')
        omitted = old_cols - withdrawn - deps
        if omitted and len(children) == 1:
            # Single-child answers inherit confirmed dependency evidence.
            # In particular, omission cannot erase a denied dependency.
            child = next(n for n in mapping if n['item_id'] == children[0])
            child['required_columns'] = sorted(set(child['required_columns']) | omitted)
            deps |= omitted
        elif omitted:
            raise ValueError('Unwithdrawn dependencies need a child after splitting')
        if withdrawn & deps:
            raise ValueError('Cannot retire a dependency still required by this parent')
    if event['kind'] == 'answer' and turn.get('selection_mode') in ('return_fields', 'operand', 'complete'):
        selected = {c for o in turn.get('selected_options', []) for c in o['required_columns']}
        purpose_role = turn.get('scoped_permission_decision', {}).get('role', turn.get('target_role'))
        children = replacements[event['target']]
        acceptable_roles = {purpose_role}
        if (compound_answers and purpose_role == 'modifier'
                and base['items'][event['target']]['role'] == 'output'):
            # A predicate operand can remain inside an output+predicate
            # container. A separate modifier child is not mandatory. This
            # does not let an output choice survive solely as a modifier.
            acceptable_roles.add(base['items'][event['target']]['role'])
        deps = {c for n in mapping if n['item_id'] in children and
                (purpose_role is None or n['role'] in acceptable_roles) for c in n['required_columns']}
        if not selected <= deps:
            raise ValueError('Selected fields must survive in the answered purpose')
    unchanged = {i: n for i, n in base['items'].items() if i not in updates}
    # Independent outputs are code-owned obligations. Do not accept a
    # filter-only survivor snapshot, relabel the filter, or restore the old
    # compound wording (which would also restore the withdrawn predicate).
    for parent in protected_outputs(base, event) & set(replacements):
        if not any(n['item_id'] in replacements[parent] and n['role'] == 'output' for n in mapping):
            raise ValueError('Preserve independent output from ' + parent
                             + '; withdraw only the asked purpose, not its output container')
    for row in mapping:
        retain_unspecified_output(row, context)
    all_rows = mapping + [main.analysis_row(n) for n in unchanged.values()]
    if not isinstance(raw['edges'], list):
        raise ValueError('Edges must be an array')
    edges = []
    for edge in raw['edges']:
        if not isinstance(edge, list) or len(edge) != 3 or any(not isinstance(v, str) for v in edge):
            raise ValueError('Each edge is [source, target, kind]')
        a, b, kind = edge
        a, b = local.get(a, a), local.get(b, b)
        if a in unchanged and b in unchanged and a not in editable and b not in editable:
            raise ValueError('Boundary edges are code-owned')
        edges.append({'source_id': a, 'target_id': b, 'kind': kind})
    edges.extend({k: e[k] for k in ('source_id', 'target_id', 'kind')}
                 for e in base['relations']['edges'] if e['source_id'] in unchanged and e['target_id'] in unchanged)
    # A one-to-one update with the same role does not need the model to copy
    # unchanged attachments. Ambiguous split attachments remain semantic work.
    for e in base['relations']['edges']:
        a, b = e['source_id'], e['target_id']
        left, right = replacements.get(a, [a]), replacements.get(b, [b])
        if len(left) == len(right) == 1:
            current = {n['item_id']: n for n in all_rows}
            if current[left[0]]['role'] == base['items'][a]['role'] and current[right[0]]['role'] == base['items'][b]['role']:
                edges.append({'source_id': left[0], 'target_id': right[0], 'kind': e['kind']})
    edges = list({(e['source_id'], e['target_id'], e['kind']): e for e in edges}.values())
    for old_edge in base['relations']['edges']:
        a, b, kind = (old_edge[k] for k in ('source_id', 'target_id', 'kind'))
        left, right = replacements.get(a, [a]), replacements.get(b, [b])
        if left and right and (a in unchanged or b in unchanged) and not any(
                e['source_id'] in left and e['target_id'] in right and e['kind'] == kind for e in edges):
            raise ValueError('Preserve surviving attachment to an unchanged node')
    # Build a compatibility frame FROM the accepted semantic rows. Models never
    # have to copy text or calculate offsets to satisfy the old verbatim parser.
    nodes = {n['item_id']: n for n in all_rows}
    text = projection(nodes, edges)
    if nodes:
        neutral = {'original_text': text, 'fragments': [{'item_id': i, 'span': n['requirement']} for i, n in nodes.items()]}
        parsed = main.validate_mapping({'items': all_rows, 'edges': edges}, neutral,
            {'current_request': text, 'public_conversation': {'turns': []}}, context)
        result_rows, checked_edges = parsed[2]['items'], parsed[3]
    else:
        result_rows, checked_edges = [], {'edges': []}
    new = deepcopy(session)
    for key in ('update_base', 'progressive_deletion_base', 'lineage_pending', 'lineage_resync_signature', 'lineage_review_signature'):
        new.pop(key, None)
    new['pending'], new['menu_frontiers'], new['menu_blockers'] = None, {}, {}
    new['permission_issues'] = {}
    active = {}
    for row in result_rows:
        iid = row['item_id']
        deps = sorted({c for b in row['bindings'] for c in b})
        active[iid] = {**row, 'role': nodes[iid]['role'], 'span': nodes[iid]['requirement'],
                      'required_columns': deps, 'denied_columns': sorted(set(deps) - set(new['allowed']))}
    if event['kind'] == 'answer' and has_query_objective(base['items'].values()) and not has_query_objective(active.values()):
        raise ValueError('An answer cannot silently delete all query objectives')
    # Refuse to lose orphaned historical evidence; this variant starts from a
    # reconciled graph and never uses text similarity for permission ownership.
    for issue in main.evidence_sources(session).values():
        owner = issue['item_id']
        if issue.get('inventory_id') != base['inventory_id'] or owner not in base['items']:
            raise ValueError('Historical permission evidence requires reconciliation before transaction')
        if not set(issue['denied_columns']) <= set(base['items'][owner]['required_columns']):
            raise ValueError('Undeclared historical dependency cannot be discarded')
    for parent, children in replacements.items():
        info['retired'][parent] = {'span': base['items'][parent]['span'], 'children': children}
    ledger, next_id = discover(active, choices, carried, info.get('next_decision_id', 1))
    info.update(replacements=replacements, event_kind=event['kind'], decision_ledger=ledger,
                next_decision_id=next_id, source_open_choices={},
                editable_ids=[i for ids in replacements.values() for i in ids], kept_ids=list(unchanged))
    if event['kind'] == 'answer':
        info['question_policy'] = 'answer_snapshot' if question_updater else 'answer_delta'
    frame = QueryFrame(text or 'No remaining query objective.',
        tuple({'item_id': i, 'span': n['span']} for i, n in active.items() if n['role'] == 'output'),
        tuple({'item_id': i, 'span': n['span']} for i, n in active.items() if n['role'] == 'modifier'))
    new['current_request'] = text
    new['view'] = {'items': active, 'frame': frame.to_json(), 'relations': checked_edges,
        'progressive': info, 'segmentation': {'mode': 'semantic_transactions', 'verbatim': False, 'inventory': {'progressive': info}},
        'inventory_id': simple_session.signature(base['inventory_id'], event, raw), 'questions': [],
        'deferred': [], 'candidate_audit': [], 'shared_decisions': [],
        'update_audit': {'mode': 'lightweight_transaction', 'event': event_payload(event), 'replacements': replacements}}
    new['removal_no_query'] = not has_query_objective(active.values())
    new.setdefault('transaction_audit', []).append({'event': event_payload(event),
        'question_effects': question_audit,
        'retire_columns': deepcopy(retire), 'replacements': replacements,
        'previous_inventory': base['inventory_id'], 'inventory': new['view']['inventory_id']})
    new = session_update.finish_view(new, context)
    main.check_active_state(new)
    return new


class LightweightUpdate(main.MainMethod):
    def __init__(self):
        super().__init__(clarity_first=True)

    def commit_transaction(self, session, base, event, raw, context):
        return transaction(session, base, event, raw, context)

    def decision_input(self, prompt, payload):
        return prompt, payload

    def compiled_decision(self, decision, source, context):
        return decision

    def prepare_menu(self, call, session, plan, context, max_options=5):
        previous = getattr(self, '_active_question_focus', None)
        previous_context = getattr(self, '_decision_context', None)
        q = self.engine.decision(session, plan)
        self._active_question_focus = q['focus']
        target = q['item']['item_id']
        edges = [e for e in session['view']['relations']['edges']
                 if target in (e['source_id'], e['target_id'])]
        neighbors = {e[k] for e in edges for k in ('source_id', 'target_id')} - {target}
        self._decision_context = {
            'nodes': [node_payload(n) for iid, n in session['view']['items'].items()
                      if iid in neighbors],
            'edges': [[e[k] for k in ('source_id', 'target_id', 'kind')] for e in edges]}
        try:
            return super().prepare_menu(call, session, plan, context, max_options)
        finally:
            self._active_question_focus = previous
            self._decision_context = previous_context

    def compile_decision(self, call, payload, context, *, field, scope=False):
        source = payload['source']
        stage = 'scope_decision' if scope else 'field_decision' if field else 'permission_decision'
        # This call decomposes ONE owner. Feeding the global request here made
        # independent sibling outputs appear in its local remainder.
        compact = {'current_request': source['requirement'], 'source': node_payload(source),
            'question_focus': source['question_focus'].get('scope' if scope else 'field',
                'Locate the contribution using a pending column.'),
            'pending_columns': payload['pending_columns'], 'decision_type': stage,
            'schema': schema_payload(context)}
        if getattr(self, '_decision_context', None):
            compact['read_only_context'] = self._decision_context
        # A compound node may own several field decisions. Locate the selected
        # purpose, not the aggregate question_focus of every same-type task.
        if stage != 'permission_decision' and getattr(self, '_active_question_focus', None):
            compact['question_focus'] = self._active_question_focus
        if stage == 'permission_decision':
            # A blocked dependency may belong to a filter inside a compound
            # owner. Its unrelated field questions must not choose this purpose.
            compact.pop('question_focus')
            compact['source'].pop('questions')
        def check(raw):
            raw = deepcopy(raw)
            status = raw.pop('scope_status')
            choices = normalize_scope_choices(raw.pop('scope_choices', None), context)
            if status == 'unresolved' and choices is not None:
                raw['scope_choices'] = choices
            if status not in ('fixed', 'unresolved'):
                raise ValueError('Invalid scope status')
            # Legacy UI calls this field "quote", but this interface has no
            # source-quote contract. Show the located business purpose rather
            # than its whole compound container. Provenance uses the source ID.
            raw['quote'] = raw.get('requirement', '')
            if source['role'] == 'modifier':
                raw['role'] = 'modifier'
            # An omitted new domain is not an access failure. Reuse only
            # already established candidates that this local purpose actually
            # declares, never arbitrary columns or join keys.
            if status == 'fixed' and not raw.get('field_candidates'):
                raw['field_candidates'] = [deepcopy(c) for c in source.get('field_candidates', [])
                    if c['column'] in raw.get('required_columns', [])]
            # This interface owns semantic purposes, not source spans. Proposed
            # schema dependencies are not historical commitments until chosen.
            # Do not reuse the old quote/subset contract and trigger a full API
            # retry for a valid join key introduced by a more precise decision.
            raw.setdefault('required_columns', [])
            raw.setdefault('field_candidates', [])
            tables = {t['name'] for t in context['tables']}
            columns = {t['name'] + '.' + c['name'] for t in context['tables'] for c in t['columns']}
            canonical = {c.casefold(): c for c in columns}
            for key in ('requirement', 'question'):
                if not isinstance(raw.get(key), str) or not raw[key].strip():
                    raise ValueError('Decision needs nonempty ' + key)
            if raw.get('role') not in ('output', 'modifier') or raw.get('kind') not in ('attribute', 'calculation', 'relation', 'instruction'):
                raise ValueError('Invalid decision role/kind')
            if not isinstance(raw.get('scope_tables'), list) or not raw['scope_tables'] or any(t not in tables for t in raw['scope_tables']):
                raise ValueError('Decision needs schema tables')
            raw['scope_tables'] = list(dict.fromkeys(raw['scope_tables']))
            if not isinstance(raw['required_columns'], list) or any(not isinstance(c, str) or c.casefold() not in canonical for c in raw['required_columns']):
                raise ValueError('Decision dependencies must be schema columns')
            raw['required_columns'] = list(dict.fromkeys(canonical[c.casefold()] for c in raw['required_columns']))
            if 'remaining_requirements' in raw and (not isinstance(raw['remaining_requirements'], list) or any(not isinstance(s, str) or not s.strip() for s in raw['remaining_requirements'])):
                raise ValueError('Independent remainders must be nonempty descriptions')
            if not isinstance(raw['field_candidates'], list):
                raise ValueError('Candidates must be a list')
            candidates = {}
            for c in raw['field_candidates']:
                if not isinstance(c, dict) or not isinstance(c.get('column'), str) or c['column'].casefold() not in canonical or not isinstance(c.get('label'), str) or not c['label'].strip():
                    raise ValueError('Candidates need schema columns and business labels')
                candidates.setdefault(canonical[c['column'].casefold()], c['label'])
            raw['field_candidates'] = [{'column': c, 'label': label} for c, label in candidates.items()]
            decision = raw
            if stage == 'permission_decision' and not set(decision['required_columns']) & set(payload['pending_columns']):
                raise ValueError('Permission purpose must own a pending dependency')
            if status == 'unresolved' and decision['field_candidates']:
                raise ValueError('Scope must be resolved before fields')
            return self.compiled_decision({**decision, 'scope_status': status,
                    'origin_dimension': 'scope' if scope else 'field' if field else 'permission'}, source, context)
        prompt, compact = self.decision_input(DECISION, compact)
        return analysis.validated_call(call, stage, prompt, compact, check)

    def analyze_flat(self, call, payload, context):
        result = super().analyze_flat(call, payload, context)
        if self._base is None:
            seed_range_choices(result)
            for row in result[2]['items']:
                row['kind'] = objective_kind(result[1][row['item_id']]['role'], row['kind'],
                    [c for b in row['bindings'] for c in b], row['question_focus'])
            info = result[4]['progressive']
            items = {r['item_id']: {**r, 'span': result[1][r['item_id']]['span']} for r in result[2]['items']}
            info['decision_ledger'], info['next_decision_id'] = discover(items, info['source_open_choices'], [], 1)
            info['question_policy'] = 'answer_delta'
        return result

    def _transaction(self, call, session, base, event, context):
        editable = region(base, event)
        edges = [e for e in base['relations']['edges'] if {e['source_id'], e['target_id']} & editable]
        visible = editable | {e[k] for e in edges for k in ('source_id', 'target_id')}
        payload = {'event': event_payload(event), 'editable_ids': sorted(editable),
            'preserve_outputs': sorted(protected_outputs(base, event) & editable),
            'nodes': [node_payload(n) for n in base['items'].values() if n['item_id'] in visible],
            'edges': [[e[k] for k in ('source_id', 'target_id', 'kind')] for e in edges],
            'schema': schema_payload(context)}
        if event['kind'] == 'answer':
            decisions = pending(base)
            payload['event']['asked_decisions'] = sorted(asked_ids(base, event, answered_dimensions(event)))
            for n in payload['nodes']:
                n.pop('questions')
                n['decisions'] = [{'id': d['decision_id'], 'dimension': d['dimension'], 'question': d['question']}
                                  for d in decisions if d['owner_id'] == n['id']]
        if event['kind'] == 'removal':
            payload['review_ids'] = sorted(shared_review_ids(base, {event['target']}))
        return analysis.validated_call(call, 'semantic_transaction', UPDATE, payload,
            lambda raw: self.commit_transaction(session, base, event, raw, context))

    def analyze_session(self, call, session, context, reconstruct=None):
        if session.get('view') is not None or session.get('removal_no_query'):
            return session
        base = session.get('update_base')
        if base is None:
            return super().analyze_session(call, session, context, reconstruct)
        turn = session['turns'][-1]
        event = {'kind': 'answer', 'target': turn['target_id'], 'dimension': turn['asked_dimension'],
                 'question': turn['question'].get('focus', turn['question']['question']), 'turn': turn}
        return self._transaction(call, session, base, event, context)

    def _remove(self, call, session, basis, q, trigger, context):
        base = basis['view']
        target = q['item']['item_id']
        if target not in base['items']:
            raise ValueError('Removal requires a current node identity')
        event = {'kind': 'removal', 'target': target, 'dimension': q['dimension'],
                 'question': q['focus'], 'decision': q.get('scoped_permission_decision', {})}
        patch = deletion_patch(base, target, event['decision'])
        new = (transaction(session, base, event, patch, context) if patch is not None else
               self._transaction(call, session, base, event, context))
        new['transaction_audit'][-1]['execution'] = 'code_deletion' if patch is not None else 'semantic_transaction'
        replacements = new['transaction_audit'][-1]['replacements']
        new.setdefault('reduction_audit', []).append({'after_turn': len(session['turns']), 'trigger': trigger,
            'target_fragment': q['item']['span'], 'target_role': q['item']['role'], 'question': q['focus'],
            'asked_dimension': q['dimension'], 'previous_request': session['current_request'],
            'request': new['current_request'], 'source_inventory': base['inventory_id'],
            'method': 'lightweight_transaction', 'scoped_permission_decision': event['decision'],
            'receipt': {'request': new['current_request'], 'drop_ids': [p for p, cs in replacements.items() if not cs],
                        'revise_ids': [p for p, cs in replacements.items() if cs]},
            'analysis_mode': 'atomic_semantic_removal'})
        return new

    def render_session(self, call, session):
        result = super().render_session(call, session)
        if result['action'] != 'deliver' or not session.get('transaction_audit'):
            return result
        nodes = list(session['view']['items'].values())
        if (len(nodes) == 1 and nodes[0]['role'] == 'output'
                and not session.get('needs_final_render')):
            # A single complete contribution already IS a natural-language
            # request. Rephrasing it adds cost and a new opportunity for drift.
            result.update(text=nodes[0]['requirement'], method='lightweight_semantic_transactions',
                          final_render='single_active_requirement')
            return result
        def check(raw):
            if not isinstance(raw, dict) or set(raw) != {'request'} or not isinstance(raw['request'], str) or not raw['request'].strip():
                raise ValueError('Expected nonempty request')
            return raw['request'].strip()
        result['text'] = analysis.validated_call(call, 'final_active_request', RENDER,
            {'nodes': [{k: n[k] for k in
                        ('item_id', 'role', 'kind', 'requirement', 'required_columns', 'missing')
                        if k in n} for n in session['view']['items'].values()],
             'edges': [[e[k] for k in ('source_id', 'target_id', 'kind')]
                      for e in session['view']['relations']['edges']],
             'deletion_rewrite': bool(session.get('needs_final_render'))}, check)
        result['method'] = 'lightweight_semantic_transactions'
        result['final_render'] = 'active_graph_model'
        return result

    @contextmanager
    def installed(self):
        # Shared-decision annotation was another API call after every inventory.
        # This variant retains linguistic propagation, without inferring equality
        # from shared columns. The scheduler still uses the normal relation graph.
        original_labels = hierarchical_questions._field_labels
        with super().installed():
            original_choices = hierarchical_questions._choices
            with (patch.object(session_update, 'annotate_shared', lambda call, s: s['view'].update(shared_decisions=[])),
                  patch.object(hierarchical_questions, '_field_labels',
                      lambda *a: reuse_field_labels(original_labels, *a)),
                  patch.object(hierarchical_questions, '_choices',
                      lambda *a: reuse_scope_choices(original_choices, *a))):
                yield self
