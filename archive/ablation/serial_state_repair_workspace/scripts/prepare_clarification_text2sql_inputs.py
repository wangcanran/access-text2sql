"""Compile deliverable clarification results into official predictor inputs.

The clarification runner emits a compact JSONL record.  The benchmark
predictor, however, needs the original instruction (schema), role policy and
metadata.  This adapter joins those immutable fields from the frozen gold
rows and replaces only ``input`` with the clarified request.  Refusals and
technical/incomplete outcomes are intentionally excluded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


DATASETS = ("bird", "spider", "livesqlbench")
LEVELS = (0, 1, 2)


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def iter_json_array(path: Path):
    """Read one complete row at a time, including rows spanning many chunks."""
    decoder = json.JSONDecoder()
    buffer = ""
    started = False
    with path.open(encoding="utf8") as stream:
        while True:
            buffer = buffer.lstrip()
            if not started and buffer:
                if buffer[0] != "[":
                    raise ValueError(f"Expected JSON array: {path}")
                buffer = buffer[1:]
                started = True
                continue
            if started and buffer.startswith(","):
                buffer = buffer[1:]
                continue
            if started and buffer.startswith("]"):
                if (buffer[1:] + stream.read()).strip():
                    raise ValueError(f"Trailing data: {path}")
                return
            if started and buffer:
                try:
                    row, end = decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    pass
                else:
                    yield row
                    buffer = buffer[end:]
                    continue
            chunk = stream.read(65536)
            if not chunk:
                raise ValueError(f"Incomplete JSON array: {path}")
            buffer += chunk


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--rows", type=Path, required=True,
                   help="Merged clarification evaluation_rows.jsonl")
    p.add_argument("--selection", type=Path, required=True,
                   help="Frozen selection.json used by the clarification run")
    p.add_argument("--gold-dir", type=Path, default=Path("data/gold/manual_flat_gold_v1"))
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        raise SystemExit(f"Output must be new or empty: {args.out}")
    args.out.mkdir(parents=True, exist_ok=True)

    selection = json.loads(args.selection.read_text(encoding="utf8"))
    selected = {(int(c["question_id"]), int(c["level"])): c for c in selection["cases"]}
    requests = read_jsonl(args.rows)
    needed = set()
    for row in requests:
        case = selected.get((int(row["question_id"]), int(row["level"])))
        if row.get("outcome") == "deliver" and (row.get("clarified_text") or "").strip() and case:
            needed.add((row["db_id"], row["role"], int(case["original_question_id"]), int(row["level"])))

    # Immutable benchmark rows are indexed by their original question and
    # role.  This prevents accidental alignment by flat row order.
    base = {}
    seen = set()
    for level in LEVELS:
        path = args.gold_dir / f"level{level}.json"
        for row in iter_json_array(path):
            md = row.get("metadata") or {}
            original_qid = md.get("question_id", md.get("flat_question_id", -1))
            key = (row["db_id"], row["role"], int(original_qid), level)
            if key in seen:
                raise ValueError(f"duplicate immutable gold key: {key}")
            seen.add(key)
            if key in needed:
                base[key] = row

    output = {dataset: {f"level{level}": [] for level in LEVELS} for dataset in DATASETS}
    included = []
    excluded = Counter()
    for row in requests:
        outcome = row.get("outcome")
        text = (row.get("clarified_text") or "").strip()
        if outcome != "deliver" or not text:
            excluded[outcome or "missing_outcome"] += 1
            continue
        case = selected.get((int(row["question_id"]), int(row["level"])))
        if case is None:
            raise ValueError(f"missing selection case for {row['id']}")
        dataset = "livesqlbench" if case.get("dataset") == "livesql" else case.get("dataset")
        if dataset not in DATASETS:
            raise ValueError(f"unsupported dataset: {case.get('dataset')}")
        key = (row["db_id"], row["role"], int(case["original_question_id"]), int(row["level"]))
        source = base.get(key)
        if source is None:
            raise ValueError(f"immutable gold row not found: {key}")
        if source["db_id"] != row["db_id"] or source["role"] != row["role"]:
            raise ValueError(f"source identity mismatch: {row['id']}")
        compiled = dict(source)
        compiled["input"] = text
        md = dict(compiled.get("metadata") or {})
        md.update({"clarification_id": row["id"], "clarification_outcome": outcome,
                   "clarification_source": str(args.rows)})
        compiled["metadata"] = md
        output[dataset][f"level{row['level']}"].append(compiled)
        included.append(row["id"])

    counts = {}
    for dataset in DATASETS:
        for level in LEVELS:
            rows = output[dataset][f"level{level}"]
            rows.sort(key=lambda r: (r["db_id"], r["role"], int((r.get("metadata") or {}).get("question_id", (r.get("metadata") or {}).get("flat_question_id", -1)))))
            path = args.out / dataset / f"level{level}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf8")
            counts[f"{dataset}/level{level}"] = len(rows)

    manifest = {"source_rows": str(args.rows), "source_sha256": sha256(args.rows),
                "selection": str(args.selection), "selection_sha256": sha256(args.selection),
                "included_outcomes": ["deliver"], "excluded_outcomes": dict(excluded),
                "counts": counts, "total": len(included),
                "note": "Only final deliver rows are sent to Text2SQL; unresolved/refuse/errors remain evaluation records."}
    (args.out / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
