"""Render all audited outputs, including rejected attempts, for human acceptance."""
import argparse
import json
from pathlib import Path


def build_report(directory):
    sections = ['# Controlled ambiguity generation',
                'Ratios count source fragments, not semantic bits. Model review is not human acceptance.']
    for path in sorted(directory.glob('[0-9]*.json')):
        trace = json.loads(path.read_text(encoding='utf8'))
        sections.extend([f"\n## {trace['db_id']} / {trace['question_id']}",
                         f"Status: {trace.get('status', 'running')}",
                         '\nOriginal:', trace['question']])
        if 'error' in trace:
            sections.append('Error: ' + trace['error'])
        if 'counting_inventory' in trace:
            sections.extend(['Counting exclusions:',json.dumps(trace['counting_inventory'],ensure_ascii=False,indent=2)])
        for row in trace.get('levels', []):
            sections.extend([f"\n### Level {row['level']}",
                             f"Target / planned / effective retention: {row.get('target_retention_ratio')} / {row.get('planned_retention_ratio')} / {row.get('effective_retention_ratio')}",
                             f"Status: {row.get('status', 'incomplete')}",
                             'Selected source specifications:'])
            for answer in row['source_specifications']:
                sections.append(f"- {answer['item_id']}: {answer['source_specification']}")
            if 'protected_fragments' in row:
                sections.append('Protected source fragments (interpret in the original sentence):')
                for item in row['protected_fragments']:
                    sections.append(f"- {item['item_id']}: {item['span']}")
            for direction in row.get('directions', {}).get('directions', []):
                sections.append(f"- {direction.get('item_id','(missing)')} ({direction.get('action','(missing)')}): {direction.get('direction','(missing)')} Retain: {direction.get('retain','(missing in model response)')}")
            sections.extend(['\nOutput:', row.get('rewritten_request', '(not produced)')])
            for index, attempt in enumerate(row.get('attempts', [])):
                sections.extend([f"\nAttempt {index} ({attempt.get('mode','primary')}):", attempt['rewritten_request'],
                                 'Protected-wording check: ' + json.dumps(attempt.get('protection_issues', [])),
                                 json.dumps(attempt.get('review', attempt.get('review_attempts', [])),
                                            ensure_ascii=False, indent=2)])
                if 'edits' in attempt:
                    sections.extend(['Exact source edits:',json.dumps(attempt['edits'],ensure_ascii=False,indent=2)])
    return '\n\n'.join(sections) + '\n'


def build_outputs_overview(directory):
    sections = ['# Outputs for acceptance',
                'Source questions and final outputs only. Ratios count fragments; statuses are model review, not human acceptance.']
    for path in sorted(directory.glob('[0-9]*.json')):
        trace = json.loads(path.read_text(encoding='utf8'))
        sections.extend([f"\n## {trace['db_id']} / {trace['question_id']}",
                         '**Original:** ' + trace['question']])
        if trace.get('error'):
            sections.append('Error: ' + trace['error'])
        for row in trace.get('levels', []):
            sections.extend([f"\n**Level {row['level']}** — {row.get('status','incomplete')}",
                f"Retention target / planned / action-based: {row.get('target_retention_ratio')} / {row.get('planned_retention_ratio')} / {row.get('effective_retention_ratio')}",
                row.get('rewritten_request','(not produced)')])
    return '\n\n'.join(sections) + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    path = args.directory / 'OUTPUTS.md'
    path.write_text(build_report(args.directory), encoding='utf8')
    (args.directory / 'OVERVIEW.md').write_text(build_outputs_overview(args.directory),encoding='utf8')
    print(path.resolve())


if __name__ == '__main__':
    main()
