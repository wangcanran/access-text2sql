"""Controlled progressive withdrawal pilot, isolated from the main method.

Both arms start from the same manual COARSE graph. A separate scripted-user
adapter points at current menu node IDs, or requests a partial withdrawal. Its
API cost is reported separately, never hidden as free semantic matching.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from clarification_main.recorder import Recorder
from clarification_main.storage import atomic_json
from clarification_main.experiments.decision_update.pacing import RateGate
from .algebra import Requirements
from .cases import cases
from .__main__ import totals


UPDATE = '''Update a request graph after the current withdrawal. Return JSON only:
{"nodes":[{"id":"n1","text":"one contribution"}],"roots":["n1"],
"requires":[["consumer","prerequisite"]]}.
Return the complete editable region; boundary nodes are read-only and may be
referenced by edges. Keep unaffected meanings, uncertainty, boolean scope and
semantic roles. Refine mixed contributions where needed and redistribute edges.
roots are independent requested outputs, population filters, grouping or ordering;
supporting subjects, associations, operands and calculation-local conditions are
dependencies. Each root must express a separately withdrawable demand. Separate
returning a value from using it as an operand or ordering key. A population
filter remains independent of outputs; a local condition belongs only to its
consumers. Compound boolean filters may stay intact.
Withdraw only the named contribution, cumulatively. Preserve surviving subjects,
shared dependencies and other demands. Removing an output does not necessarily
remove its calculation. Removing a population restriction does not remove the
subject or association. Do not introduce unstated fields, formulas, thresholds,
SQL policies or interpretations of vague terms. Do not turn filters into outputs.
Return surviving meanings only; keep withdrawal protocol out of node text.
When editing a compound containing several independent demands, return each
surviving demand as its own root, not combined wording with just a phrase removed.
Untouched compounds may remain coarse. A demand can retain its own qualifications;
these do not make it several independent demands.
Requires edges mean semantic prerequisites; use a directed acyclic graph. Avoid
depending on an independently withdrawable display demand when the shared
computation or context can be represented separately. No exact quote matching.
'''
ROUTE = '''Translate a scripted withdrawal into a CURRENT menu target. Return JSON
{"mode":"whole|partial","node_ids":["id"]}. You are an experimental user adapter,
not the updater. The intended withdrawal is provided; do not decide graph edits.
Use whole ONLY when the intended withdrawal exactly matches the ENTIRE meaning
of one or more current independent roots; do not include their dependencies.
If it is only part of a compound node, touches a support node, or the match is
uncertain, use partial and point to the smallest relevant current nodes. Do not
select a larger demand merely because it contains the requested words.
Withdrawing an output or calculation withdraws that whole requested result with
its own local qualifications; the intent need not repeat those qualifications.
This does not withdraw shared supports or independent population filters.
Use partial if the same node also contains a DIFFERENT independent demand.
Use only
current IDs; no explanations, invented IDs, or text matching constraints.
'''
RENDER = '''Write one natural-language database query request from the provided
independent_requirements. Return JSON only: {"request":"..."}.
These requirements determine what to return, filter, group and order.
support_only supplies subjects, operands and qualifications through requires
edges (consumer, prerequisite); it does not request additional return values.
For example, a calculation needed only for ordering is not a returned value.
Preserve scope, ambiguity, quantities, AND/OR and negation. Do not add definitions,
values, requirements or withdrawal instructions.
'''


def fixtures():
    raw = {c['name']: c for c in cases()}
    result = []
    specs = [
        ('finance', 'client_binding_finance',
         [('s', 'The client associated with a specified loan application'),
          ('p', 'Return birthday, age at loan application and location for that client'),
          ('f', 'Return financial activity prior to the loan, a quantitative financial ratio, '
           'balance range, relevant payment or credit account information, and a summary of prior borrowing activity')],
         ['p', 'f'], [['p', 's'], ['f', 's']]),
        ('shared', 'shared_local',
         [('s', 'Accounts opened in 2020'), ('i', 'Return account identifiers'),
          ('b', 'Return minimum and maximum balances computed from transactions before the loan')],
         ['i', 'b'], [['i', 's'], ['b', 's']]),
        ('existence', 'independent_existence',
         [('s', 'Accounts opened in 2020 that have at least one transaction before their loan'),
          ('i', 'Return account identifiers'),
          ('b', 'Return minimum and maximum balances over those pre-loan transactions')],
         ['i', 'b'], [['i', 's'], ['b', 's']]),
        ('order', 'independent_group_order',
         [('s', 'Districts and their schools and scores'),
          ('o', 'Return district name, school count and average score'),
          ('r', 'Group by district and order districts by school count descending')],
         ['o', 'r'], [['o', 's'], ['r', 's']]),
    ]
    for name, key, nodes, roots, edges in specs:
        c = raw[key]
        result.append({'name': name, 'source': c['source'], 'request': c['request'],
                       'graph': {'nodes': [dict(id=i, text=t) for i, t in nodes],
                                 'roots': roots, 'requires': edges},
                       'withdrawals': [s['meaning'] for s in c['steps']]})
    return result


def validate(graph):
    if not isinstance(graph, dict) or set(graph) != {'nodes', 'roots', 'requires'}:
        raise ValueError('Expected nodes, roots and requires')
    nodes = graph['nodes']
    if not isinstance(nodes, list) or any(not isinstance(n, dict) or set(n) != {'id', 'text'}
        or not isinstance(n['id'], str) or not n['id'] or not isinstance(n['text'], str)
        or not n['text'].strip() for n in nodes):
        raise ValueError('Invalid graph nodes')
    if len({n['id'] for n in nodes}) != len(nodes):
        raise ValueError('Duplicate node ID')
    req = Requirements.parse({k: graph[k] for k in ('roots', 'requires')}, {n['id'] for n in nodes})
    return req


def active(graph):
    req = validate(graph)
    ids = req.live()
    return {'nodes': [deepcopy(n) for n in graph['nodes'] if n['id'] in ids],
            'roots': sorted(req.roots), 'requires': [list(e) for e in sorted(req.requires) if set(e) <= ids]}


def withdraw_code(graph, ids):
    """Only exact root handles are eligible; no word/role heuristics or cascade."""
    req = validate(graph)
    if not ids or not set(ids) <= req.roots:
        raise ValueError('Withdrawal must target current independent roots')
    remaining = req.withdraw(ids)
    # Keep inactive text in storage, but all planning/rendering uses active().
    return {'nodes': deepcopy(graph['nodes']), 'roots': sorted(remaining.roots),
            'requires': [list(e) for e in sorted(remaining.requires)]}


def render_payload(graph):
    view = active(graph)
    roots = set(view['roots'])
    return {
        'independent_requirements': [n for n in view['nodes'] if n['id'] in roots],
        'support_only': [n for n in view['nodes'] if n['id'] not in roots],
        'requires': view['requires'],
    }


def connected(graph, seeds):
    reached = set(seeds)
    while True:
        more = {i for e in graph['requires'] if set(e) & reached for i in e} - reached
        if not more:
            return reached
        reached |= more


def update(call, graph, request, withdrawals, route):
    current = active(graph)
    editable = connected(current, route['node_ids'])
    boundary = [n for n in current['nodes'] if n['id'] not in editable]
    payload = {'original_request': request, 'withdrawals': withdrawals,
               'current_withdrawal': withdrawals[-1],
               'editable_nodes': [n for n in current['nodes'] if n['id'] in editable],
               'boundary_nodes': boundary, 'roots': current['roots'], 'requires': current['requires']}
    raw = call('hybrid_update', UPDATE, payload)
    if not isinstance(raw, dict) or set(raw) != {'nodes', 'roots', 'requires'}:
        raise ValueError('Expected replacement graph')
    outside = {n['id'] for n in boundary}
    if any(n['id'] in outside for n in raw['nodes']):
        raise ValueError('Cannot replace boundary node')
    local = {n['id'] for n in raw['nodes']}
    if not set(raw['roots']) <= local or any(not set(e) & local for e in raw['requires']):
        raise ValueError('Replacement edges/roots outside editable region')
    merged = {'nodes': boundary + raw['nodes'],
              'roots': sorted((set(current['roots']) & outside) | set(raw['roots'])),
              'requires': [e for e in current['requires'] if set(e) <= outside] + raw['requires']}
    validate(merged)
    return merged, {'editable_nodes': len(editable), 'boundary_nodes': len(boundary)}


def route_event(call, graph, meaning):
    view = active(graph)
    r = call('hybrid_user_adapter', ROUTE, {'withdrawal': meaning, 'current_menu_graph': view})
    if not isinstance(r, dict) or set(r) != {'mode', 'node_ids'} or r['mode'] not in ('whole', 'partial'):
        raise ValueError('Invalid current menu selection')
    ids = r['node_ids']
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise ValueError('Selection IDs required')
    if len(set(ids)) != len(ids) or not set(ids) <= {n['id'] for n in view['nodes']}:
        raise ValueError('Selection must reference current nodes')
    if r['mode'] == 'whole' and not set(ids) <= set(view['roots']):
        raise ValueError('Whole selection must reference independent roots')
    return r


def run_arm(c, arm, recorder, out):
    path = out / f"{c['name']}_{arm}.json"
    record = {'question_id': c['name'], 'arm': arm, 'model': recorder.model, 'calls': [],
              'steps': [], 'initial_graph': c['graph'], 'request': c['request'], 'source': c['source']}
    graph = deepcopy(c['graph'])
    try:
        for index, meaning in enumerate(c['withdrawals'], 1):
            call = recorder.bind(record, path, index, arm)
            route = route_event(call, graph, meaning)
            before = active(graph)
            action, fallback = 'model', None
            if arm == 'hybrid' and route['mode'] == 'whole':
                try:
                    graph = withdraw_code(graph, route['node_ids'])
                    action = 'code'
                except ValueError as exc:
                    fallback = str(exc)
            region_info = None
            if action == 'model':
                graph, region_info = update(call, graph, c['request'], c['withdrawals'][:index], route)
            view = active(graph)
            record['steps'].append({'index': index, 'withdrawal': meaning, 'route': route,
                'action': action, 'fallback': fallback, 'region': region_info, 'before': before,
                'stored_graph': deepcopy(graph), 'active_graph': view,
                'next_menu': [n for n in view['nodes'] if n['id'] in view['roots']]})
            atomic_json(path, record)
        record['final_graph'] = active(graph)
        if not record['final_graph']['roots']:
            record.update(status='refuse', final_request=None, complete=True)
        else:
            call = recorder.bind(record, path, len(c['withdrawals']) + 1, arm)
            raw = call('hybrid_render', RENDER, render_payload(record['final_graph']))
            if not isinstance(raw, dict) or set(raw) != {'request'} or not isinstance(raw['request'], str) or not raw['request'].strip():
                raise ValueError('Expected final request')
            record.update(status='delivered', final_request=raw['request'], complete=True)
    except Exception as exc:
        record.update(status='technical_failure', complete=False, error=f'{type(exc).__name__}: {exc}')
    record['cost'] = totals(record)
    for label, predicate in (
        ('adapter_cost', lambda c: c['stage'] == 'hybrid_user_adapter'),
        ('update_cost', lambda c: c['stage'] == 'hybrid_update'),
        ('render_cost', lambda c: c['stage'] == 'hybrid_render')):
        record[label] = totals({'calls': [c for c in record['calls'] if predicate(c)]})
    atomic_json(path, record)
    print(json.dumps({k: record.get(k) for k in ('question_id', 'arm', 'status', 'final_request', 'cost', 'error')}, ensure_ascii=False), flush=True)
    return record


def report(out, rows):
    atomic_json(out / 'SUMMARY.json', [{k: r.get(k) for k in
        ('question_id', 'arm', 'status', 'cost', 'adapter_cost', 'update_cost', 'render_cost', 'final_request', 'error')}
        for r in rows])
    lines = ['# 按语义边界混合撤销：受控 live', '',
        '初始为人工粗图。没有精细语义清单。固定撤销脚本，不是自主菜单/权限端到端实验。',
        'adapter 只把脚本意图映射到当前节点；其调用和成本单独报告。主方法菜单本应自带节点 ID。',
        'model 组每步调用更新；hybrid 组完整根用代码，复合/依赖冲突才调用模型。', '',
        '相同输入复用响应。下表为实际新请求，不能将复用的零成本当作独立运行成本。', '',
        '| 案例 | 路径 | 结果 | 代码撤销 | 更新新请求 | 全部新请求 | 新请求 tokens |',
        '|---|---|---|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['question_id']} | {r['arm']} | {r['status']} | "
                     f"{sum(s['action']=='code' for s in r['steps'])} | {r['update_cost']['requests']} | "
                     f"{r['cost']['requests']} | {r['cost']['reported_tokens']} |")
    lines += ['', '## 逻辑工作量（含复用响应）', '',
        '| 案例 | 路径 | adapter 调用 | 更新调用 | 生成调用 | 全部 tokens |',
        '|---|---|---:|---:|---:|---:|']
    for r in rows:
        calls = r['calls']
        counts = [sum(c['stage'] == stage for c in calls) for stage in
                  ('hybrid_user_adapter', 'hybrid_update', 'hybrid_render')]
        tokens = sum((c.get('usage') or {}).get('total_tokens', 0) for c in calls)
        lines.append(f"| {r['question_id']} | {r['arm']} | {counts[0]} | {counts[1]} | {counts[2]} | {tokens} |")
    for r in rows:
        lines += ['', f"## {r['question_id']} / {r['arm']}", '', r.get('final_request') or r.get('error', r['status'])]
        for s in r['steps']:
            lines += ['', f"### {s['index']}. {s['withdrawal']} — {s['action']}", '',
                      '选择：' + json.dumps(s['route'], ensure_ascii=False), '', '下一菜单：', '']
            lines += ['- ' + n['id'] + ': ' + n['text'] for n in s['next_menu']]
            lines += ['', '活动依赖：', '']
            by_id = {n['id']: n['text'] for n in s['active_graph']['nodes']}
            lines += ['- ' + by_id[a] + ' → ' + by_id[b] for a,b in s['active_graph']['requires']]
    (out / 'RESULTS.md').write_text('\n'.join(lines) + '\n', encoding='utf8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--live', action='store_true')
    p.add_argument('--model', default='gpt-5.5')
    p.add_argument('--cases')
    p.add_argument('--max-calls', type=int, default=40)
    p.add_argument('--reuse-dir', type=Path, action='append', default=[])
    args = p.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError('Use a new output directory')
    selected = fixtures()
    if args.cases:
        names = set(args.cases.split(','))
        if not names <= {c['name'] for c in selected}:
            raise ValueError('Unknown case')
        selected = [c for c in selected if c['name'] in names]
    for c in selected:
        validate(c['graph'])
    args.out.mkdir(parents=True)
    atomic_json(args.out / 'FIXTURES.json', selected)
    atomic_json(args.out / 'CONFIG.json', {'model': args.model, 'scripted_withdrawals': True,
        'manual_coarse_graph': True, 'fine_inventory': False, 'schema_permissions_sql': False,
        'main_method_changed': False, 'reasoning_effort': 'low', 'semantic_retries': 0})
    snapshot = args.out / 'sources'
    snapshot.mkdir()
    for f in Path(__file__).parent.glob('*.py'):
        (snapshot / f.name).write_bytes(f.read_bytes())
    if not args.live:
        print('Coarse fixtures validated')
        return
    recorder = Recorder(args.model, args.max_calls, args.reuse_dir, empty_retries=1,
        transport_retries=0, stage_reasoning={s: 'low' for s in
        ('hybrid_user_adapter', 'hybrid_update', 'hybrid_render')})
    recorder.client.chat.completions.create = RateGate(2).wrap(recorder.client.chat.completions.create)
    rows = []
    for index, c in enumerate(selected):
        for arm in (('hybrid', 'model') if index % 2 == 0 else ('model', 'hybrid')):
            rows.append(run_arm(c, arm, recorder, args.out))
            report(args.out, rows)


if __name__ == '__main__':
    main()
