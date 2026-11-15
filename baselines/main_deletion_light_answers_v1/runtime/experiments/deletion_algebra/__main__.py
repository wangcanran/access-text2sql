"""Controlled live comparison of repeated snapshots vs one analysis + code."""
import argparse
from copy import deepcopy
import itertools
import json
from pathlib import Path
import time

from clarification_main.recorder import Recorder
from clarification_main.storage import atomic_json
from clarification_main.experiments.decision_update.pacing import RateGate
from .algebra import Requirements
from .cases import cases


CONTRACT = '''Return only JSON {"roots":["id"],"requires":[["consumer","prerequisite"]]}.
Use IDs from atoms. roots are independently requested outputs, filters, grouping
or ordering. Auxiliary subjects, associations, calculation operands and local
calculation restrictions are prerequisites, not additional independent requests.
A requires pair means the consumer needs the prerequisite with its stated scope.
Keep independent population filters even if a related output is removed. A
calculation-only filter disappears only after its last consumer disappears.
Preserve shared subjects, associations, result instructions, ambiguity and AND/OR
or negation exactly. Returning a calculation and using it for ordering are separate
requirements. Never turn a condition into an output. Do not resolve vague words,
change the subject, invent requirements or add inferred SQL policies.
The supplied atom inventory is fixed for this controlled test. It is not a list of
independent demands: identify their ownership and dependency from the request.
Omit inactive dependencies. Any equivalent transitive dependency representation
is acceptable; no source-span matching is required.
'''
NORMALIZE = 'Represent the original request without withdrawing anything.\n' + CONTRACT
SNAPSHOT = '''Update the remaining requirements after the withdrawals. Return the
whole surviving structure. Each withdrawal removes only its named contribution;
retain all other independent requirements and the dependencies they still need.
Withdrawals are cumulative. Removing an unspecified loan's identifying restriction
does not remove its still-needed client/loan association. Do not consult permission
or SQL.\n''' + CONTRACT


def signature(graph):
    """Semantic scope signature; accepts redundant equivalent transitive edges."""
    return {root: sorted(Requirements(graph.inventory, frozenset({root}), graph.requires).live())
            for root in sorted(graph.roots)}


def compare(predicted, expected):
    actual, gold = predicted.live(), expected.live()
    return {'exact_active': actual == gold,
            'exact_roots': predicted.roots == expected.roots,
            'exact_scope': signature(predicted) == signature(expected),
            'missing': sorted(gold - actual), 'extra': sorted(actual - gold),
            'missing_roots': sorted(expected.roots - predicted.roots),
            'extra_roots': sorted(predicted.roots - expected.roots),
            'active': sorted(actual), 'expected_active': sorted(gold)}


def offline(c):
    original = Requirements.parse(c['expected'], c['atoms'])
    current = original
    steps = []
    for step in c['steps']:
        current = current.withdraw(step['withdraw'])
        assert current.withdraw(step['withdraw']) == current
        steps.append(current.json())
    finals = []
    for permutation in itertools.permutations(c['steps']):
        other = original
        for step in permutation:
            other = other.withdraw(step['withdraw'])
        finals.append(other)
    assert all(other == current for other in finals)
    return {'idempotence': True, 'independent_order_invariance': True,
            'permutations_checked': len(finals), 'steps': steps}


def totals(record):
    fresh = [x for x in record['calls'] if 'reused_from' not in x]
    return {'requests': len(fresh),
            'reported_tokens': sum((x.get('usage') or {}).get('total_tokens', 0) for x in fresh),
            'api_seconds': sum(x.get('elapsed_seconds', 0) for x in fresh),
            'response_failures': sum(x.get('response_status') != 'valid_json_object' for x in fresh),
            'reused_responses': sum('reused_from' in x for x in record['calls'])}


def run_arm(c, arm, recorder, out):
    path = out / f"{c['name']}_{arm}.json"
    record = {'question_id': c['name'], 'model': recorder.model, 'arm': arm,
              'source': c['source'], 'calls': [], 'steps': [], 'contract_retries': 0}
    call = recorder.bind(record, path, 0, arm)
    gold = Requirements.parse(c['expected'], c['atoms'])
    payload = {'request': c['request'], 'atoms': c['atoms']}
    initial = None
    if arm == 'algebra':
        try:
            initial = Requirements.parse(call('withdrawal_normalization', NORMALIZE, payload), c['atoms'])
            record['initial'] = {'graph': initial.json(), **compare(initial, gold)}
        except Exception as exc:
            record['error'] = f'{type(exc).__name__}: {exc}'
    history, previous = [], None
    for index, step in enumerate(c['steps']):
        gold = gold.withdraw(step['withdraw'])
        history.append(step)
        entry = {'index': index + 1, 'withdrawal': step}
        try:
            start = time.perf_counter()
            if arm == 'algebra':
                if initial is None:
                    raise ValueError('Normalization unavailable')
                initial = initial.withdraw(step['withdraw'])
                predicted = initial
            else:
                raw = call('withdrawal_snapshot', SNAPSHOT, {**payload,
                    'withdrawals': deepcopy(history), 'previous_structure': previous})
                predicted = Requirements.parse(raw, c['atoms'])
                previous = predicted.json()
            entry.update(graph=predicted.json(), **compare(predicted, gold))
            entry['local_seconds_including_api'] = time.perf_counter() - start
        except Exception as exc:
            entry.update(error=f'{type(exc).__name__}: {exc}',
                         exact_active=False, exact_roots=False, exact_scope=False)
        record['steps'].append(entry)
        atomic_json(path, record)
    record['cost'] = totals(record)
    atomic_json(path, record)
    return record


def report(out, selected, records):
    rows = []
    for arm in ('algebra', 'snapshot'):
        rs = [r for r in records if r['arm'] == arm]
        steps = [s for r in rs for s in r['steps']]
        rows.append({'arm': arm, 'cases': len(rs), 'deletions': len(steps),
                     'exact_active': sum(s['exact_active'] for s in steps),
                     'exact_scope': sum(s['exact_scope'] for s in steps),
                     'all_steps_exact_scope': sum(all(s['exact_scope'] for s in r['steps']) for r in rs),
                     **{k: sum(r['cost'][k] for r in rs)
                        for k in ('requests', 'reported_tokens', 'api_seconds', 'response_failures')}})
    atomic_json(out / 'METRICS.json', {'summary': rows, 'case_count': len(selected),
        'limits': ['Fixed human-authored atoms; extraction and decomposition are not tested.',
                   'Snapshot is an isolated semantic-update baseline, not the existing session fast path.',
                   'No permissions, menus, simulator, text rendering or SQL execution is evaluated.',
                   'Single run, eight selected stress cases; provider-reported usage is not a billing audit.']})
    lines = ['# 删除代数：受控实验', '',
             '比较同一语义原子清单、同一删除顺序；algebra 一次分析后代码删除，snapshot 每次由模型返回剩余结构。', '',
             '**人工提供了语义原子清单。模型只判断独立要求和依赖，不测试从原文自动细拆。**', '',
             '| 方法 | 全部步骤作用域正确的案例 | 活跃语义正确步骤 | 作用域正确步骤 | API请求 | 报告tokens | API秒 |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['arm']} | {r['all_steps_exact_scope']}/{r['cases']} | "
                     f"{r['exact_active']}/{r['deletions']} | {r['exact_scope']}/{r['deletions']} | "
                     f"{r['requests']} | {r['reported_tokens']} | {r['api_seconds']:.2f} |")
    for c in selected:
        lines.extend(['', f"## {c['name']} ({c['source']})", '', c['request'], ''])
        for r in (x for x in records if x['question_id'] == c['name']):
            lines.append(f"**{r['arm']}**")
            if 'initial' in r:
                lines.append(f"初始依赖作用域正确：{r['initial']['exact_scope']}。")
            if 'error' in r:
                lines.append('初始化错误：' + r['error'])
            for s in r['steps']:
                active = s.get('active', [])
                lines.extend(['', f"第{s['index']}步：撤销 {s['withdrawal']['meaning']}",
                    f"作用域正确：{s['exact_scope']}；缺失：{s.get('missing', [])}；多余：{s.get('extra', [])}。",
                    '保留语义：' + '；'.join(c['atoms'][i] for i in active)])
                if 'error' in s:
                    lines.append(s['error'])
    lines.extend(['', '## 解释边界', '',
        '这些结果仅衡量给定原子清单后的语义归属和删除。未测试自动细拆、文本生成、权限、菜单、SQL或端到端会话。',
        'snapshot 是逐次模型更新的受控基线；不能将其调用数当成现有主链路调用数。旧链路已有代码删除路径。',
        'AND/OR/否定保留在原子内部。本轮只撤销整个复合筛选，不支持撤销复合表达式内部某个分支。',
        '正确性来自预先人工标注的独立根及每个根的依赖可达集合，不以文字相似度或本轮模型自评判定。'])
    (out / 'RESULTS.md').write_text('\n'.join(lines), encoding='utf8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--live', action='store_true')
    p.add_argument('--free', action='store_true',
                   help='Free-decomposition pilot: send only request, no atom inventory')
    p.add_argument('--deferred', action='store_true',
                   help='Coarse-graph pilot: mark withdrawals, refine before planning, clean at end')
    p.add_argument('--cases', help='Comma separated case names; defaults to all')
    p.add_argument('--model', default='gpt-5.5')
    p.add_argument('--reuse-dir', type=Path, action='append', default=[])
    p.add_argument('--max-calls', type=int, default=32)
    args = p.parse_args()
    if args.deferred:
        if args.free:
            p.error('--free and --deferred are different experiments')
        from .deferred import run
        run(args)
        return
    if args.out.exists() and any(args.out.iterdir()):
        p.error('Output directory must be empty or new')
    args.out.mkdir(parents=True, exist_ok=True)
    selected = cases()
    if args.cases:
        names = set(args.cases.split(','))
        if not names <= {c['name'] for c in selected}:
            p.error('Unknown case')
        selected = [c for c in selected if c['name'] in names]
    atomic_json(args.out / 'CASES_PRIVATE_GOLD.json', selected)
    atomic_json(args.out / 'OFFLINE.json', {c['name']: offline(c) for c in selected})
    atomic_json(args.out / 'CONFIG.json', {'model': args.model, 'live': args.live,
        'fixed_human_atoms': not args.free, 'free_decomposition': args.free,
        'gold_graph_never_in_payload': True,
        'semantic_contract_retries': 0, 'cases': [c['name'] for c in selected]})
    if not args.live:
        print(f'Offline algebra checks passed for {len(selected)} cases', flush=True)
        return
    if args.free:
        from .free_inventory import run
        run(args, selected)
        return
    recorder = Recorder(args.model, args.max_calls, args.reuse_dir,
                        empty_retries=1, transport_retries=0)
    recorder.client.chat.completions.create = RateGate(2).wrap(recorder.client.chat.completions.create)
    records = []
    for c in selected:
        # Alternate which arm runs first to avoid a uniform temporal order.
        arms = ('algebra', 'snapshot') if len(records) % 4 == 0 else ('snapshot', 'algebra')
        for arm in arms:
            records.append(run_arm(c, arm, recorder, args.out))
            report(args.out, selected, records)
    print(json.dumps(json.loads((args.out / 'METRICS.json').read_text(encoding='utf8'))['summary'], indent=2))


if __name__ == '__main__':
    main()
