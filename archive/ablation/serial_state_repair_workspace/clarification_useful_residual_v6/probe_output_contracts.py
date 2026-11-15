"""Offline wire-contract replay; does NOT claim end-to-end task success."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .output_contracts import compile_output, OutputContractError, STAGE_FAMILIES, VERSION


def replay(traces):
    accepted, absent, stages, failures, wire_failures = 0, 0, Counter(), [], []
    records = sorted(traces.glob('*_reduction.json'))
    for path in records:
        record = json.loads(path.read_text(encoding='utf8'))
        for index, call in enumerate(record['calls']):
            if 'response' not in call:
                absent += 1
                continue
            stage = call['stage'].removesuffix('_contract_retry')
            stages[stage] += 1
            wire = call['response']['choices'][0]['message']['content']
            # The current transport supplies a parsed dict. Report extra wire
            # parsing findings separately; this layer cannot reconstruct keys
            # already discarded by the upstream parser.
            try:
                compile_output(stage, wire)
            except OutputContractError as exc:
                if exc.path == '$' and ('duplicate JSON key' in exc.reason or 'invalid JSON' in exc.reason):
                    wire_failures.append(dict(case=path.stem, call_index=index, reason=exc.reason))
            raw = json.loads(wire)
            before = deepcopy(raw)
            try:
                compiled = compile_output(stage, raw)
            except OutputContractError as exc:
                failures.append(dict(case=path.stem, call_index=index, stage=call['stage'],
                                     path=exc.path, reason=exc.reason))
            else:
                value = compiled.to_legacy()
                assert compile_output(stage, value).to_legacy() == value, (path, index, 'not idempotent')
                accepted += 1
            assert raw == before, (path, index, 'input mutated')
    return dict(boundary_version=VERSION, trace_directory=str(traces.resolve()), cases=len(records),
                responses=sum(stages.values()), accepted_shapes=accepted,
                rejected_shapes=len(failures), calls_without_response=absent,
                stages=dict(stages), unregistered_stages=sorted(set(stages) - set(STAGE_FAMILIES)),
                rejections=failures, upstream_wire_parse_findings=wire_failures,
                boundary_source_sha256=hashlib.sha256(
                    Path(__file__).with_name('output_contracts.py').read_bytes()).hexdigest(),
                limitations='Wire parsing/shape/idempotence only; no model calls, state replay, semantic or SQL evaluation.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--traces', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not args.traces.is_dir() or not any(args.traces.glob('*_reduction.json')):
        parser.error('--traces must contain completed *_reduction.json records')
    result = replay(args.traces)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'OUTPUT_BOUNDARY_REPLAY.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({k: result[k] for k in ('cases', 'responses', 'accepted_shapes',
        'rejected_shapes', 'calls_without_response', 'unregistered_stages')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
