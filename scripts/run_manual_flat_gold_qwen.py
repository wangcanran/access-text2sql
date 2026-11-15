"""Run the manually certified flat gold set with a local OpenAI-compatible model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


DATASETS = (("bird", "bird", 1335), ("spider", "spider", 1291),
            ("livesqlbench", "livesql", 1405))
LEVELS = ("level0", "level1", "level2")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="Qwen3.8-27B")
    parser.add_argument("--client", choices=("qwen38", "qwen38_clean", "generic", "deepseek"), default="qwen38",
                        help="qwen38 preserves its original request; qwen38_clean keeps non-thinking but removes sampling penalties; generic uses shared zero-shot settings; deepseek disables thinking")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--request-timeout", type=int, default=1800)
    parser.add_argument("--save-every", type=int, default=25)
    parser.add_argument("--smoke", action="store_true", help="Run one row per dataset and level")
    args = parser.parse_args()

    if min(args.workers, args.max_tokens, args.request_timeout, args.save_every) < 1:
        parser.error("workers, max-tokens, request-timeout and save-every must be positive")

    manifest_path = args.input_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    counts = {name: manifest["datasets"][manifest_name]["rows_per_level"]
              for name, manifest_name, _ in DATASETS}
    if tuple(counts[name] for name, _, _ in DATASETS) != tuple(count for _, _, count in DATASETS):
        raise ValueError(f"Unexpected per-dataset row counts: {counts}")
    if sum(counts.values()) != manifest["rows_by_level"]["0"]:
        raise ValueError("Manifest row totals do not match per-dataset counts")

    args.out.mkdir(parents=True, exist_ok=True)
    split_dir = args.out / "inputs"
    split_dir.mkdir(exist_ok=True)
    input_hashes = {}
    row_counts = {}
    for level in LEVELS:
        path = args.input_dir / f"{level}.json"
        rows = json.loads(path.read_text(encoding="utf8"))
        if len(rows) != sum(counts.values()):
            raise ValueError(f"{level} has {len(rows)} rows; manifest has {sum(counts.values())}")
        offset = 0
        for dataset, _, _ in DATASETS:
            batch = rows[offset:offset + counts[dataset]]
            offset += counts[dataset]
            if args.smoke:
                batch = batch[:1]
            target = split_dir / dataset / f"{level}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf8")
            input_hashes[f"{dataset}/{level}"] = sha256(target)
            row_counts[f"{dataset}/{level}"] = len(batch)

    config = {
        "model": args.model,
        "api_base": os.environ.get("RBAC_API_BASE", "http://127.0.0.1:8000/v1"),
        "temperature": 0.0,
        "thinking": "off" if args.client in ("qwen38", "qwen38_clean", "deepseek") else "model_default",
        "client": args.client,
        "request_parameters": ({
            "temperature": 0.0,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 1.5,
            "repetition_penalty": 1.0,
            "seed": 20261030,
            "chat_template_kwargs": {"enable_thinking": False},
        } if args.client == "qwen38" else ({"temperature": 0.0, "max_tokens": args.max_tokens,
                                              "chat_template_kwargs": {"enable_thinking": False}}
                                              if args.client == "qwen38_clean" else {"temperature": 0.0, "max_tokens": args.max_tokens,
                                               **({"thinking": {"type": "disabled"}} if args.client == "deepseek" else {})})),
        "workers": args.workers,
        "max_tokens": args.max_tokens,
        "request_timeout": args.request_timeout,
        "save_every": args.save_every,
        "datasets": [name for name, _, _ in DATASETS],
        "levels": list(LEVELS),
        "row_counts": row_counts,
        "input_hashes": input_hashes,
        "manifest_sha256": sha256(manifest_path),
        "smoke": args.smoke,
        "started_at": time.time(),
    }
    config_path = args.out / "RUN_CONFIG.json"
    if config_path.exists():
        previous = json.loads(config_path.read_text(encoding="utf8"))
        for key in ("model", "temperature", "thinking", "client", "request_parameters", "input_hashes", "smoke"):
            if previous.get(key) != config[key]:
                raise ValueError(f"Cannot resume with changed {key}; use a new output directory")
    else:
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf8")

    for dataset, _, _ in DATASETS:
        for level in LEVELS:
            source = split_dir / dataset / f"{level}.json"
            stem = args.out / dataset / level
            stem.parent.mkdir(parents=True, exist_ok=True)
            output = stem.with_suffix(".sql")
            env = dict(os.environ)
            env["RBAC_API_BASE"] = config["api_base"]
            env["RBAC_REQUEST_TIMEOUT"] = str(args.request_timeout)
            if args.client == "deepseek":
                if args.model not in ("deepseek-v4-pro", "deepseek-flash"):
                    raise ValueError("The paired DeepSeek baseline requires Pro or Flash")
                env["RBAC_DEEPSEEK_THINKING"] = "disabled"
            if args.client == "qwen38_clean":
                env["RBAC_QWEN_CLEAN"] = "1"
            env["RBAC_RESPONSE_AUDIT"] = str(stem.with_name(level + "_responses.jsonl"))
            command = [
                sys.executable, ("scripts/predict_rbac_gold_qwen.py" if args.client in ("qwen38", "qwen38_clean")
                                 else "scripts/predict_rbac_endpoint.py"),
                "--provider", "openai", "--model", args.model,
                "--dataset", dataset, "--mode", "rbac", "--structured",
                "--shot_num", "0", "--temperature", "0", "--max_tokens", str(args.max_tokens),
                "--workers", str(args.workers), "--rate_limit", "0", "--save_every", str(args.save_every),
                "--resume", "--input", str(source), "--output", str(output),
            ]
            log_path = stem.with_name(level + "_inference.log")
            with log_path.open("a", encoding="utf8") as log:
                subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)

            detailed_path = stem.with_name(level + "_detailed.json")
            detailed = json.loads(detailed_path.read_text(encoding="utf8"))
            expected = row_counts[f"{dataset}/{level}"]
            if len(detailed) != expected:
                raise RuntimeError(f"{dataset}/{level}: expected {expected} predictions, got {len(detailed)}")
            if len(output.read_text(encoding="utf8").splitlines()) != expected:
                raise RuntimeError(f"{dataset}/{level}: SQL output line count mismatch")
            failures = [i for i, row in enumerate(detailed) if not row.get("raw_response", "").strip()
                        or row["raw_response"].startswith("Error:")]
            audit = {
                "dataset": dataset,
                "level": level,
                "expected": expected,
                "predictions": len(detailed),
                "technical_or_empty_responses": failures,
                "prediction_sha256": sha256(output),
                "completed_at": time.time(),
            }
            stem.with_name(level + "_integrity.json").write_text(
                json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf8")
            print(f"COMPLETE {dataset}/{level}: {expected} rows; errors={len(failures)}", flush=True)

    (args.out / "COMPLETE.json").write_text(json.dumps({
        "completed_at": time.time(), "datasets": config["datasets"],
        "levels": list(LEVELS), "smoke": args.smoke,
    }, indent=2), encoding="utf8")


if __name__ == "__main__":
    main()
