import json
from .storage import atomic_json

def summarize(out):
    rows, lines = [], ['# 澄清完整会话对照', '',
        '不含最终用户复核，不生成或执行 SQL。相同模型输入复用配对结果；token 同时报告逻辑成本和实际新调用成本。', '']
    for path in sorted(out.glob('*_*_*.json')):
        r = json.loads(path.read_text(encoding='utf8'))
        if 'arm' not in r:
            continue
        episode = r.get('episode', {})
        session = episode.get('session', {})
        reductions = session.get('reduction_audit', [])
        delivery = r.get('delivery', {})
        items = (session.get('view') or {}).get('items', {})
        row = {'case': f"{r['question_id']}_{r['level']}", 'arm': r['arm'],
            'outcome': r.get('outcome', 'running'), 'turns': len(episode.get('interactions', [])),
            'reductions': len(reductions), 'reason': delivery.get('reason') or r.get('stop_reason') or r.get('error', ''),
            'delivery': delivery.get('text', ''), 'available_for_text2sql': delivery.get('available_for_text2sql', False),
            'declared_denied_columns': sorted({c for n in items.values() for c in n['denied_columns']}),
            'cost': r.get('cost'), 'actual_cost': r.get('actual_cost'),
            'frozen_unchanged': r.get('frozen_unchanged')}
        row['connected_updates'] = session.get('connected_update_audit', [])
        row['authoritative_state'] = r.get('config', {}).get('authoritative_state', False)
        row['method_config'] = r.get('config', {})
        row['scoped_update_audit'] = session.get('scoped_update_audit', [])
        row['prior_turns'] = r.get('resume', {}).get('prior_turns', 0)
        row['new_reductions'] = len(reductions) - r.get('resume', {}).get('prior_reductions', 0)
        row['incomplete_calls'] = r.get('incomplete_calls', [])
        row['cost_note'] = r.get('cost_note', '')
        rows.append(row)
        lines += [f"## {row['case']} / {r['arm']}", '', '**输入**：' + r['request'], '',
            '**原始需求（模拟用户私有）**：' + r['reference_request'], '']
        for index, turn in enumerate(episode.get('interactions', []), 1):
            lines += [f'### 第 {index} 轮', '', '```json',
                json.dumps(turn, ensure_ascii=False, indent=2), '```', '']
        lines += ['### 需求删除记录', '', '```json', json.dumps(reductions, ensure_ascii=False, indent=2),
            '```', '', '### 结果', '', '```json', json.dumps(row, ensure_ascii=False, indent=2), '```', '']
    atomic_json(out / 'SUMMARY.json', rows)
    (out / 'DIALOGUES.md').write_text('\n'.join(lines), encoding='utf8')
    return rows

