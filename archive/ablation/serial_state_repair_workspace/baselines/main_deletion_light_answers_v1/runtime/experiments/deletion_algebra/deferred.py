"""Controlled coarse-graph pilot; no main-method runtime changes.

The fixtures supply coarse graphs, never a fine-grained reference graph. Answers
and withdrawals are scripted interventions, not simulated full conversations.
Semantic projection is required before planning after any new withdrawal. Merely
keeping an exclusion ledger is NOT considered a valid active query graph.
"""
from copy import deepcopy
import json
from pathlib import Path

from clarification_main.recorder import Recorder
from clarification_main.storage import atomic_json, digest
from clarification_main.experiments.decision_update.pacing import RateGate
from .cases import cases
from .__main__ import totals


UPDATE = '''Update the editable region of a database request graph using the
original request and effective events. Refine connected compound requirements
where needed to separate surviving, answered, withdrawn and shared contributions.
Redistribute their edges; children do not automatically inherit all parent edges.
Keep unaffected meanings, unresolved details, AND/OR, negation and scope. A
condition or entity must not become an extra output. An answer resolves only its
actual question. Never infer unstated fields, values, formulas or scope.

Each withdrawal removes only the named contribution. It is not an unanswered
question. Never ask for it again or use it to constrain a surviving contribution.
Preserve shared dependencies still needed by other contributions. Distinguish a
withdrawable population restriction from a required subject, association or
computation operand. Withdrawal of a restriction does not withdraw its subject.
Use needs for necessary operands/bindings, restricts for a population selection
(condition -> population), and qualifies for calculation-local qualification
(condition -> computation). Removing a displayed value need not remove its use
in another surviving requirement. Do not silently resolve remaining ambiguities.

Return JSON only:
{"nodes":[{"id":"local1","text":"one contribution","kind":"output|filter|context|computation|compound",
"state":"active|withdrawn"}],
"edges":[["consumer or condition ID","provider or subject ID","needs|restricts|qualifies"]],
"next_questions":[{"owner_ids":["local1"],"question":"a remaining business question"}]}.
Return the complete replacement region, not the boundary nodes. Boundary IDs can
be referenced by edges/questions but never replaced. Use unique region IDs.
Edges must touch a returned node. A surviving node must not need a withdrawn node.
An active node must contain only surviving meaning, so split a mixed compound
before labeling its pieces. Retain separate contexts when other nodes need them.
Next questions must concern surviving unresolved meaning only; this is a planning
probe, not permission to add information to the request. No source quote matching.
'''
EAGER = '''Physically omit withdrawn contributions and unused local dependencies
from the replacement graph immediately. Return only active nodes.\n'''
DEFERRED = '''Retain withdrawn contributions as separate withdrawn nodes where
represented in this region. They are tombstones, not active requirements. Do not
keep withdrawn words mixed into an active compound. Actual physical cleanup is
deferred; effective exclusion and boundary refinement are required now.\n'''
RENDER = '''Write one natural-language database query request from the active
graph only. Preserve all surviving outputs, filters, subjects, calculations,
qualifications and uncertainty with their scope. Do not add inferred definitions,
new outputs, values or disambiguations. Return only JSON: {"request":"..."}.'''


def fixture(name, request, nodes, edges, events, source):
    graph = {'nodes': [{'id': i, 'text': text, 'kind': 'compound', 'state': 'active'}
                       for i, text in nodes], 'edges': edges}
    return dict(name=name, request=request, graph=graph, events=events, source=source)


def withdrawal(target, anchor):
    return {'kind': 'withdrawal', 'meaning': target, 'anchor_ids': [anchor]}


def answer(question, text, anchor):
    return {'kind': 'answer', 'question': question, 'answer': text, 'anchor_ids': [anchor]}


def fixtures():
    original = {c['name']: c for c in cases()}
    finance = original['client_binding_finance']
    shared = original['shared_local']
    hero = original['hero_shared_scope']
    return [
        fixture('finance', finance['request'], [
            ('s', 'The client associated with a specified loan application'),
            ('p', 'Return their birthday, age at the time of loan application, and location'),
            ('f', 'Return their financial activity prior to the loan, a quantitative financial ratio, '
                  'balance range, information about relevant payment or credit accounts, '
                  'and a summary of prior borrowing activity')],
            [['p', 's', 'needs'], ['f', 's', 'needs']], [
                withdrawal('Return client birthday', 'p'),
                withdrawal('Restrict to the specified, still unidentified loan application', 's'),
                {'kind': 'checkpoint'},
                answer('At what point should the client age be reported?',
                       'At the time of loan application.', 'p'),
                withdrawal('Return client location', 'p'),
                {'kind': 'checkpoint'},
                answer('What time scope applies to the financial activity?',
                       'Financial activity prior to the loan.', 'f')], finance['source']),
        fixture('shared', shared['request'], [
            ('s', 'Accounts opened in 2020'),
            ('i', 'Return account identifiers'),
            ('b', 'Return minimum and maximum balances computed from transactions before the loan')],
            [['i', 's', 'needs'], ['b', 's', 'needs']], [
                withdrawal('Return minimum balance', 'b'),
                {'kind': 'checkpoint'},
                answer('How should the maximum balance be computed?',
                       'The maximum balance over transactions before the loan.', 'b'),
                withdrawal('Return maximum balance', 'b'),
                {'kind': 'checkpoint'},
                answer('What should still be returned for the accounts?', 'Account identifiers.', 'i')],
                shared['source']),
        fixture('hero', hero['request'], [
            ('s', '3-D Man'), ('o', 'State all attributes along with their values')],
            [['o', 's', 'needs']], [
                withdrawal('Return corresponding attribute values', 'o'),
                {'kind': 'checkpoint'},
                answer('Which hero are the requested attributes for?', '3-D Man.', 's')], hero['source']),
    ]


def validate_graph(raw):
    if not isinstance(raw, dict) or set(raw) != {'nodes', 'edges'}:
        raise ValueError('Expected nodes and edges')
    nodes, edges = raw['nodes'], raw['edges']
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError('nodes and edges must be arrays')
    by_id = {}
    for n in nodes:
        if (not isinstance(n, dict) or set(n) != {'id', 'text', 'kind', 'state'}
                or not isinstance(n['id'], str) or not n['id'] or n['id'] in by_id
                or not isinstance(n['text'], str) or not n['text'].strip()
                or n['kind'] not in ('output', 'filter', 'context', 'computation', 'compound')
                or n['state'] not in ('active', 'withdrawn')):
            raise ValueError('Invalid graph node')
        by_id[n['id']] = n
    for e in edges:
        if (not isinstance(e, list) or len(e) != 3 or not all(isinstance(x, str) for x in e)
                or e[0] not in by_id or e[1] not in by_id or e[0] == e[1]
                or e[2] not in ('needs', 'restricts', 'qualifies')):
            raise ValueError('Invalid graph edge')
        if (e[2] == 'needs' and by_id[e[0]]['state'] == 'active'
                and by_id[e[1]]['state'] == 'withdrawn'):
            raise ValueError('Active node needs a withdrawn dependency')
    return deepcopy(raw)


def active_graph(graph):
    graph = validate_graph(graph)
    ids = {n['id'] for n in graph['nodes'] if n['state'] == 'active'}
    return {'nodes': [n for n in graph['nodes'] if n['id'] in ids],
            'edges': [e for e in graph['edges'] if set(e[:2]) <= ids]}


def region(graph, seeds):
    ids = {n['id'] for n in graph['nodes']}
    reached = set(seeds) & ids
    if not reached:
        raise ValueError('Event has no current anchor')
    while True:
        more = set().union(*(set(e[:2]) for e in graph['edges'] if set(e[:2]) & reached)) - reached
        if not more:
            return reached
        reached |= more


def new_state(c):
    graph = validate_graph(c['graph'])
    return {'graph': graph, 'events': [], 'projected_events': 0, 'next_questions': [],
            'aliases': {n['id']: [n['id']] for n in graph['nodes']}, 'revisions': []}


def append_event(state, event):
    state = deepcopy(state)
    state['events'].append(deepcopy(event))
    # No question generated before an exclusion may be reused after it. The
    # mixed graph is explicitly dirty until a semantic projection succeeds.
    state['next_questions'] = []
    return state


def planning_view(state):
    if state['projected_events'] != len(state['events']):
        raise ValueError('Pending semantic projection; stale graph cannot drive planning')
    return {'graph': active_graph(state['graph']), 'next_questions': deepcopy(state['next_questions'])}


def project(call, c, state, arm):
    previous = deepcopy(state)
    pending = state['events'][state['projected_events']:]
    if not pending:
        return state
    seeds = set()
    for e in pending:
        for anchor in e['anchor_ids']:
            seeds.update(state['aliases'].get(anchor, []))
    # A referenced original contribution may have disappeared in an earlier
    # refinement. Re-examine the connected original context, never guess by text.
    if not seeds:
        seeds = {n['id'] for n in state['graph']['nodes']}
    editable = region(state['graph'], seeds)
    boundary = [n for n in state['graph']['nodes'] if n['id'] not in editable]
    payload = {'original_request': c['request'], 'events': state['events'],
               'pending_events': pending,
               'editable_nodes': [n for n in state['graph']['nodes'] if n['id'] in editable],
               'boundary_nodes': boundary,
               'edges': state['graph']['edges']}
    raw = call('deferred_projection' if arm == 'deferred' else 'eager_projection',
               UPDATE + (DEFERRED if arm == 'deferred' else EAGER), payload)
    if not isinstance(raw, dict) or set(raw) != {'nodes', 'edges', 'next_questions'}:
        raise ValueError('Expected graph and next_questions')
    local = {n['id'] for n in raw['nodes']}
    outside = {n['id'] for n in boundary}
    if local & outside or any(not (set(e[:2]) & local) for e in raw['edges']):
        raise ValueError('Boundary is read-only')
    revision = len(state['revisions']) + 1
    names = {i: f'r{revision}_{i}' for i in local}
    nodes = [{**n, 'id': names[n['id']]} for n in raw['nodes']]
    edges = [[names.get(a, a), names.get(b, b), k] for a, b, k in raw['edges']]
    edges += [e for e in state['graph']['edges'] if set(e[:2]) <= outside]
    graph = validate_graph({'nodes': boundary + nodes, 'edges': edges})
    active_ids = {n['id'] for n in graph['nodes'] if n['state'] == 'active'}
    questions = []
    if not isinstance(raw['next_questions'], list):
        raise ValueError('next_questions must be an array')
    for q in raw['next_questions']:
        if (not isinstance(q, dict) or set(q) != {'owner_ids', 'question'}
                or not isinstance(q['owner_ids'], list) or not q['owner_ids']
                or not all(isinstance(i, str) for i in q['owner_ids'])
                or not isinstance(q['question'], str) or not q['question'].strip()):
            raise ValueError('Invalid next question')
        owners = [names.get(i, i) for i in q['owner_ids']]
        if not set(owners) <= active_ids:
            raise ValueError('Question points to inactive node')
        questions.append({**q, 'owner_ids': owners})
    result = deepcopy(state)
    result.update(graph=graph, projected_events=len(state['events']), next_questions=questions)
    # Conservative region provenance, not a semantic one-to-one alignment. The
    # pilot can revisit more nodes than necessary, and records that cost/limit.
    for original, descendants in result['aliases'].items():
        if set(descendants) & editable:
            result['aliases'][original] = sorted((set(descendants) - editable) | set(names.values()))
    result['revisions'].append({'editable_count': len(editable), 'boundary_count': len(boundary),
                                'replacement_count': len(nodes), 'pending_event_count': len(pending),
                                'before_digest': digest(previous['graph'])})
    return result


def run_arm(c, arm, recorder, out):
    path = out / f"{c['name']}_{arm}.json"
    record = {'question_id': c['name'], 'model': recorder.model, 'arm': arm,
              'source': c['source'], 'calls': [], 'steps': [], 'initial_graph': c['graph'],
              'scripted_events': c['events'], 'semantic_contract_retries': 0}
    state = new_state(c)
    try:
        for index, event in enumerate(c['events'], 1):
            call = recorder.bind(record, path, index, arm)
            if event['kind'] == 'checkpoint':
                if arm == 'deferred':
                    state = project(call, c, state, arm)
                view = planning_view(state)
            else:
                state = append_event(state, event)
                if arm == 'eager' or event['kind'] == 'answer':
                    state = project(call, c, state, arm)
                view = (planning_view(state)
                        if state['projected_events'] == len(state['events']) else None)
            record['steps'].append({'index': index, 'event': event, 'state': deepcopy(state),
                                    'planning_view': view})
            atomic_json(path, record)
        call = recorder.bind(record, path, len(c['events']) + 1, arm)
        state = project(call, c, state, arm)
        clean = planning_view(state)['graph']
        raw = call('deferred_final_render', RENDER, {'active_graph': clean})
        if (not isinstance(raw, dict) or set(raw) != {'request'}
                or not isinstance(raw['request'], str) or not raw['request'].strip()):
            raise ValueError('Expected final request')
        record.update(final_graph=clean, final_request=raw['request'], structural_valid=True,
                      tombstones_cleaned=len(state['graph']['nodes']) - len(clean['nodes']))
    except Exception as exc:
        record.update(error=f'{type(exc).__name__}: {exc}', structural_valid=False)
    record['cost'] = totals(record)
    atomic_json(path, record)
    print(json.dumps({'case': c['name'], 'arm': arm, 'valid': record['structural_valid'],
                      'cost': record['cost'], 'final': record.get('final_request'),
                      'error': record.get('error')}, ensure_ascii=False), flush=True)
    return record


def write_report(out, records):
    summary = []
    for arm in ('eager', 'deferred'):
        selected = [r for r in records if r['arm'] == arm]
        summary.append({'arm': arm, 'cases': len(selected),
                        'structurally_complete': sum(r['structural_valid'] for r in selected),
                        **{k: sum(r['cost'][k] for r in selected) for k in
                           ('requests', 'reported_tokens', 'api_seconds', 'response_failures')}})
    atomic_json(out / 'METRICS.json', {'summary': summary,
                'semantic_quality': 'Requires manual review of graphs, scopes, plans and final text; not auto-scored.'})
    lines = ['# 标记撤销与渐进细化：受控 live', '',
             '粗图由实验固定；没有提供精细语义清单或标准细图。事件是脚本干预，非真实模拟会话。',
             'eager 每次撤销立即投影；deferred 先记录，下一次规划前必须完成投影。两者回答后都更新。',
             '后续问题和回答预先固定；next_questions 仅作为撤销后规划的观察探针，不实际决定脚本。',
             '无 schema、权限、候选菜单、模拟用户或 SQL；不能声称端到端恢复率或主链路降本。', '',
             '| 路径 | 结构完成 | 请求数 | 报告 tokens | API 秒 |',
             '|---|---:|---:|---:|---:|']
    for r in summary:
        lines.append(f"| {r['arm']} | {r['structurally_complete']}/{r['cases']} | "
                     f"{r['requests']} | {r['reported_tokens']} | {r['api_seconds']:.2f} |")
    for r in records:
        lines.extend(['', f"## {r['question_id']} / {r['arm']}", '',
                      r.get('final_request', r.get('error', '')), '', '最终活动节点：'])
        for n in r.get('final_graph', {}).get('nodes', []):
            lines.append(f"- {n['kind']}: {n['text']}")
        for s in r['steps']:
            if s['event']['kind'] == 'checkpoint':
                lines.extend(['', f"规划检查点 {s['index']}："])
                for q in s['planning_view']['next_questions']:
                    lines.append('- ' + q['question'])
    (out / 'RESULTS.md').write_text('\n'.join(lines) + '\n', encoding='utf8')


def run(args):
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError('Output directory must be empty or new')
    args.out.mkdir(parents=True, exist_ok=True)
    selected = fixtures()
    if args.cases:
        names = set(args.cases.split(','))
        if not names <= {c['name'] for c in selected}:
            raise ValueError('Deferred cases are finance,shared,hero')
        selected = [c for c in selected if c['name'] in names]
    for c in selected:
        new_state(c)
    atomic_json(args.out / 'FIXTURES.json', selected)
    atomic_json(args.out / 'CONFIG.json', {'model': args.model, 'live': args.live,
                'coarse_graphs_supplied': True, 'fine_inventory_supplied': False,
                'scripted_events': True, 'gold_sql': False, 'main_method_changed': False,
                'semantic_contract_retries': 0, 'cases': [c['name'] for c in selected]})
    snapshot = args.out / 'sources'
    snapshot.mkdir()
    for p in Path(__file__).parent.glob('*.py'):
        (snapshot / p.name).write_bytes(p.read_bytes())
    if not args.live:
        print(f'{len(selected)} coarse fixtures structurally validated', flush=True)
        return
    recorder = Recorder(args.model, args.max_calls, args.reuse_dir,
                        empty_retries=1, transport_retries=0)
    recorder.client.chat.completions.create = RateGate(2).wrap(recorder.client.chat.completions.create)
    records = []
    for index, c in enumerate(selected):
        for arm in (('deferred', 'eager') if index % 2 == 0 else ('eager', 'deferred')):
            records.append(run_arm(c, arm, recorder, args.out))
            write_report(args.out, records)

