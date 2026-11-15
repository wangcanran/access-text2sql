"""Explain saved coarse-response contract failures without modifying traces."""
from pathlib import Path
import collections
import json
import re
import time


def explain(raw, request):
    issues = []
    if not isinstance(raw, dict):
        return ['response is not an object']
    if set(raw) != {'original_text', 'fragments'}:
        issues.append('top-level keys differ from original_text/fragments')
    if raw.get('original_text') != request:
        issues.append('original_text differs from the actual request')
    rows = raw.get('fragments')
    if not isinstance(rows, list) or not rows:
        return issues + ['fragments must be a nonempty array']
    seen = set()
    for index, row in enumerate(rows):
        prefix = f'fragments[{index}]'
        if not isinstance(row, dict):
            issues.append(prefix + ' is not an object')
            continue
        extra = sorted(set(row) - {'item_id', 'span', 'open_choices'})
        missing = sorted({'item_id', 'span', 'open_choices'} - set(row))
        if extra:
            issues.append(prefix + ' unexpected keys: ' + ', '.join(extra))
        if missing:
            issues.append(prefix + ' missing keys: ' + ', '.join(missing))
        identity = row.get('item_id')
        if not isinstance(identity, str) or not re.fullmatch(r'f[1-9][0-9]*', identity):
            issues.append(prefix + ' needs a neutral f1/f2/... identity')
        elif identity in seen:
            issues.append(prefix + ' duplicates an identity')
        else:
            seen.add(identity)
        span = row.get('span')
        if not isinstance(span, str) or not span.strip() or span not in request:
            issues.append(prefix + ' span is not a nonempty exact request substring')
        choices = row.get('open_choices')
        if not isinstance(choices, list):
            issues.append(prefix + ' open_choices must be an array')
            continue
        for j, choice in enumerate(choices):
            at = f'{prefix}.open_choices[{j}]'
            if not isinstance(choice, dict):
                issues.append(at + ' is not an object')
                continue
            extra = sorted(set(choice) - {'dimension', 'span', 'question'})
            missing = sorted({'dimension', 'span', 'question'} - set(choice))
            if extra:
                issues.append(at + ' unexpected keys: ' + ', '.join(extra)
                              + '; remove these keys; the only allowed keys are dimension, span, question')
            if missing:
                issues.append(at + ' missing keys: ' + ', '.join(missing))
            if choice.get('dimension') not in ('scope', 'field', 'operation', 'value'):
                issues.append(at + ' dimension is unsupported')
            quote = choice.get('span')
            if not isinstance(quote, str) or not quote.strip() or not isinstance(span, str) or quote not in span:
                issues.append(at + ' quote is not inside its owning fragment; copy a nonempty exact substring of owner span='
                              + json.dumps(span, ensure_ascii=False))
            if not isinstance(choice.get('question'), str) or not choice['question'].strip():
                issues.append(at + ' question is empty or not a string')
    return issues


def explain_wire(raw, request):
    """Use the same wire compiler and typed-choice normalization as production."""
    from clarification_useful_residual_v6.output_contracts import compile_output
    from clarification_contract_v5.contracts import normalize_choices
    try:
        legacy = normalize_choices(compile_output('progressive_coarse', raw).to_legacy())
    except (ValueError, TypeError, KeyError) as exc:
        return [str(exc)]
    return explain(legacy, request)


def main():
    job = Path('/path/to/access/output/four_arm_terminal_20261106')
    records = []
    for arm in ('no_pruning', 'no_clarification', 'clarify_then_prune'):
        folders = sorted(p for p in (job/arm/'retries').glob('round_*') if p.is_dir())
        for folder in folders[-2:]:
            for path in folder.glob('*_reduction.json'):
                trace = json.loads(path.read_text())
                if 'ablation_sources_unchanged' not in trace or trace.get('outcome') in ('deliver', 'refuse'):
                    continue
                for call in trace.get('calls', []):
                    if call.get('stage') != 'progressive_coarse_contract_retry':
                        continue
                    response = call.get('response') or {}
                    try:
                        raw = json.loads(response['choices'][0]['message']['content'])
                    except (KeyError, IndexError, TypeError, ValueError):
                        continue
                    records.append(dict(arm=arm, question_id=trace['question_id'], level=trace['level'],
                                        trace=str(path), issues=explain_wire(raw, trace['request'])))
    report = dict(at=time.time(), scope='Last two observed retry folders per arm; coarse JSON diagnostics only',
                  records=records, validation_or_results_changed=False)
    (job/'COARSE_FAILURE_SHAPES.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(dict(records=len(records), issues=dict(collections.Counter(
        issue for record in records for issue in record['issues']))), ensure_ascii=False))


if __name__ == '__main__':
    main()
