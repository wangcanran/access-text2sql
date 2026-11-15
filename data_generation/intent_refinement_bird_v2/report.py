"""Stable JSON report serialization for stage-8 runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .evaluate import CaseEvaluation
from .metrics import aggregate_metrics


def build_report(results: Iterable[CaseEvaluation]) -> dict:
    rows = tuple(results)
    return {
        "metrics": aggregate_metrics(rows).to_dict(),
        "cases": [item.to_dict() for item in rows],
    }


def write_report(path: str | Path, results: Iterable[CaseEvaluation]) -> None:
    target = Path(path)
    target.write_text(json.dumps(build_report(results), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

