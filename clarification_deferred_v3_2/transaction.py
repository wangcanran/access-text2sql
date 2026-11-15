"""Owned transaction runtime: frozen semantics, canonical namespace allocation.

Extracted from main_deletion_light_answers_v1's lightweight.transaction.
Only ID allocation changes: eligible projection is not the session namespace.
The immutable baseline file remains untouched.
"""
import re
from copy import deepcopy
from clarification_deferred_deletion.baseline import light

QueryFrame = light.QueryFrame
answered_dimensions = light.answered_dimensions
apply_questions = light.apply_questions
discover = light.discover
event_payload = light.event_payload
has_query_objective = light.has_query_objective
main = light.main
normalize_transaction = light.normalize_transaction
objective_kind = light.objective_kind
pending = light.pending
projection = light.projection
protected_outputs = light.protected_outputs
region = light.region
retain_unspecified_output = light.retain_unspecified_output
session_update = light.session_update
shared_review_ids = light.shared_review_ids
simple_session = light.simple_session


def allocated_ids(session, base):
    """Reserve IDs across active, parked and historical nodes, including roots."""
    info = base.get('progressive', {})
    state = session.get('deferred_state', {})
    result = set(base['items']) | set(info.get('parents', {})) | set(info.get('retired', {}))
    result.update(p for p in info.get('parents', {}).values() if isinstance(p, str))
    for key in ('blocked_nodes', 'canonical_nodes'):
        result.update(state.get(key, {}))
    return result


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
    if not isinstance(raw, dict) or 'updates' not in raw or set(raw) - {
            'updates', 'edges', 'retire_columns', 'question_changes', 'settled_decisions'}:
        raise ValueError('Expected updates and optional edges/retire_columns/question_changes/settled_decisions')
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
    used = allocated_ids(session, base)
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
                    or temp in used and temp != parent):
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
            raise ValueError('This answer cannot retire confirmed dependencies; preserve the existing returned object and its columns')
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
    edges, edge_endpoint_resolutions = [], []
    for edge in raw['edges']:
        if not isinstance(edge, list) or len(edge) != 3 or any(not isinstance(v, str) for v in edge):
            raise ValueError('Each edge is [source, target, kind]')
        a, b, kind = edge
        a, b = local.get(a, a), local.get(b, b)
        for endpoint, column in (('source', 'a'), ('target', 'b')):
            value = a if column == 'a' else b
            children = replacements.get(value)
            if children is not None:
                if len(children) != 1:
                    raise ValueError('Edge endpoint refers to a split parent without a child alias: '
                                     + value)
                replacement = children[0]
                edge_endpoint_resolutions.append({
                    'endpoint': endpoint, 'retired_parent': value,
                    'replacement': replacement, 'kind': kind})
                if column == 'a':
                    a = replacement
                else:
                    b = replacement
        if a == b:
            edge_endpoint_resolutions.append({'effect': 'elided_self_relation',
                                              'node_id': a, 'kind': kind})
            continue
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
        'edge_endpoint_resolutions': edge_endpoint_resolutions,
        'retire_columns': deepcopy(retire), 'replacements': replacements,
        'previous_inventory': base['inventory_id'], 'inventory': new['view']['inventory_id']})
    new = session_update.finish_view(new, context)
    main.check_active_state(new)
    return new
