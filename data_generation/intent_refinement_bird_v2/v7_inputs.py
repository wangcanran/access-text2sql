"""Adapter for disclosure-level-generation-v7 JSONL outputs.

The v7 file contains four disclosure surfaces for each underlying query.  A
surface is an evaluation case in its own right: its ``natural_language`` is
the public initial request, while ``evaluation_only`` remains private and is
used only by the simulator/evaluator.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import EvaluationCase
from .fixtures import build_evaluation_case, extract_sqlite_schema, normalize_policy
from .user_renderer import UserRenderer


@dataclass(frozen=True)
class V7LoadStats:
    rows_read: int = 0
    cases_loaded: int = 0
    malformed_rows: int = 0
    skipped_levels: int = 0
    skipped_duplicates: int = 0


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def load_bird_role_rows(path: str | Path, *, db_root: str | Path | None = None) -> dict[tuple[str, str], tuple[dict[str, Any], ...]]:
    """Index the original role dataset by ``db_id + original question``.

    The generated v7 file intentionally removed role context.  This index
    restores it from the source export while taking complete schema/FKs from
    the corresponding SQLite database directory.
    """
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("role dataset must be a JSON list")
    output: dict[tuple[str, str], list[dict[str, Any]]] = {}
    schema_cache: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        db_id = str(raw.get("db_id") or "")
        question = raw.get("input") or raw.get("original_question") or raw.get("question")
        if not db_id or not question:
            continue
        enriched = dict(raw)
        if db_root and db_id:
            db_path = Path(db_root) / db_id / f"{db_id}.sqlite"
            if db_id not in schema_cache and db_path.exists():
                schema_cache[db_id] = extract_sqlite_schema(db_path)
            schema = schema_cache.get(db_id)
            if schema:
                enriched["_schema_tables"] = schema["tables"]
                enriched["_foreign_keys"] = schema["foreign_keys"]
        key = (db_id, _norm_text(question))
        output.setdefault(key, []).append(enriched)
    return {key: tuple(value) for key, value in output.items()}


def _level_value(row: Mapping[str, Any]) -> int:
    value = row.get("level")
    if isinstance(value, bool):
        raise ValueError("level must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("level is missing or not an integer") from exc


def _with_case_id(row: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a row and make each disclosure surface addressable."""
    copied = dict(row)
    source_id = str(row.get("source_id") or row.get("case_id") or "v7")
    # ``build_public_case`` uses source_id as its stable case identifier.
    # Replace it in this adapter so level surfaces cannot collide; retain the
    # original source id in metadata/evaluation fields for later grouping.
    copied["source_id"] = f"{source_id}:level-{_level_value(row)}"
    copied["case_id"] = copied["source_id"]
    return copied


def load_v7_evaluation_cases(
    path: str | Path,
    *,
    role: str = "bird_reader",
    policy_provider: Any | None = None,
    db_root: str | Path | None = None,
    levels: Iterable[int] | None = None,
    limit: int | None = None,
    unique_query: bool = False,
    role_rows: Mapping[tuple[str, str], tuple[Mapping[str, Any], ...]] | None = None,
    description_root: str | Path | None = None,
    prefer_original_sources: bool = True,
) -> tuple[tuple[EvaluationCase, ...], V7LoadStats]:
    """Load v7 rows without exposing private fields in the public case.

    ``policy_provider`` receives the raw v7 row and may return a mapping of
    table to accessible columns.  If omitted, the public policy defaults to
    all columns, which is appropriate for a parser/Text2SQL smoke test but
    not for a role-level security evaluation.
    """
    selected_levels = {int(value) for value in levels} if levels is not None else None
    seen_queries: set[str] = set()
    cases: list[EvaluationCase] = []
    rows_read = malformed = skipped_levels = skipped_duplicates = 0
    source = Path(path)
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows_read += 1
        try:
            row = json.loads(line)
            if not isinstance(row, Mapping):
                raise ValueError("row must be an object")
            level = _level_value(row)
            if selected_levels is not None and level not in selected_levels:
                skipped_levels += 1
                continue
            source_id = str(row.get("source_id") or row.get("case_id") or "v7")
            query_key = source_id
            if unique_query and query_key in seen_queries:
                skipped_duplicates += 1
                continue
            seen_queries.add(query_key)
            prepared = _with_case_id(row)
            original_question = str(((row.get("evaluation_only") or {}).get("original_questions") or [""])[0])
            role_map = role_rows or {}
            matches = tuple(role_map.get((str(row.get("db_id") or ""), _norm_text(original_question)), ())) if prefer_original_sources else ()
            sources = matches or (prepared,)
            for source_row in sources:
                merged = dict(prepared)
                if source_row is not prepared:
                    # Role rows are authoritative for role/policy and the
                    # original BIRD question; v7 remains authoritative for
                    # the four-level public natural language.
                    merged["role"] = source_row.get("role")
                    merged["policy"] = source_row.get("policy")
                    metadata = dict(source_row.get("metadata") or {})
                    merged["original_request"] = source_row.get("input") or source_row.get("original_question")
                    merged["evidence"] = source_row.get("metadata", {}).get("evidence", "") if isinstance(source_row.get("metadata"), Mapping) else ""
                    merged["schema"] = {
                        "tables": source_row.get("_schema_tables") or ((merged.get("evaluation_only") or {}).get("schema") or {}).get("tables", {}),
                        "foreign_keys": source_row.get("_foreign_keys") or ((merged.get("evaluation_only") or {}).get("schema") or {}).get("foreign_keys", ()),
                    }
                    merged["evaluation_only"] = {
                        **dict(merged.get("evaluation_only") or {}),
                        "gold_sql": metadata.get("gold_sql") or original_sql,
                        "original_questions": [str(merged["original_request"] or "")],
                        "schema": {
                            "tables": merged["schema"]["tables"],
                            "foreign_keys": merged["schema"]["foreign_keys"],
                        },
                    }
                    role_name = str(source_row.get("role") or role)
                else:
                    role_name = role
                if source_row is not prepared:
                    merged["source_id"] = f"{prepared['source_id']}:role-{role_name}"
                    merged["case_id"] = merged["source_id"]
                policy = policy_provider(merged) if callable(policy_provider) else merged.get("policy")
                db_path = None
                if db_root and row.get("db_id"):
                    candidate_db = Path(db_root) / str(row["db_id"]) / f"{row['db_id']}.sqlite"
                    db_path = candidate_db if candidate_db.exists() else None
                case = build_evaluation_case(merged, role=role_name, policy=policy, db_path=db_path, profile=None)
                if description_root:
                    # Store catalog metadata on the public case for callers
                    # that construct UserRenderer.from_bird_root later.
                    public = case.public
                    metadata = dict(public.metadata)
                    metadata["description_root"] = str(description_root)
                    case = type(case)(
                        public=type(public)(public.case_id, public.initial_request, public.schema, public.foreign_keys, public.role, public.policy, metadata),
                        original_request=case.original_request, gold_sql=case.gold_sql, pseudo_sql=case.pseudo_sql,
                        profile=case.profile, private_metadata=case.private_metadata,
                    )
                cases.append(case)
                if limit is not None and len(cases) >= limit:
                    break
            if limit is not None and len(cases) >= limit:
                break
        except (OSError, TypeError, ValueError, json.JSONDecodeError, KeyError):
            malformed += 1
    stats = V7LoadStats(rows_read, len(cases), malformed, skipped_levels, skipped_duplicates)
    return tuple(cases), stats


def load_bird_v7_cases(
    v7_path: str | Path,
    *,
    role_dataset_path: str | Path,
    bird_database_root: str | Path,
    levels: Iterable[int] | None = None,
    limit: int | None = None,
    unique_query: bool = False,
    role: str = "bird_reader",
    policy_provider: Any | None = None,
) -> tuple[tuple[EvaluationCase, ...], V7LoadStats]:
    """Load generated v7 surfaces joined with original BIRD role records.

    ``bird_database_root`` must contain ``<db_id>/<db_id>.sqlite`` and each
    database's ``database_description`` directory.  The returned public case
    keeps complete schema/FK metadata for the parser/Text2SQL boundary while
    role columns remain available only to the internal permission validator.
    """
    root = Path(bird_database_root)
    role_rows = load_bird_role_rows(role_dataset_path, db_root=root)
    return load_v7_evaluation_cases(
        v7_path,
        role=role,
        policy_provider=policy_provider,
        db_root=root,
        levels=levels,
        limit=limit,
        unique_query=unique_query,
        role_rows=role_rows,
        description_root=root,
        prefer_original_sources=True,
    )


def v7_all_columns_policy(row: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """Return a full-access policy for smoke tests only."""
    evaluation = row.get("evaluation_only") or {}
    schema = evaluation.get("schema") or row.get("schema") or {}
    tables = schema.get("tables", schema) if isinstance(schema, Mapping) else {}
    return normalize_policy(tables, {"tables": tables})


def renderer_for_bird_v7_case(case: EvaluationCase) -> UserRenderer:
    """Build the user-side renderer backed by the original BIRD descriptions."""
    root = case.public.metadata.get("description_root")
    db_id = case.public.metadata.get("db_id")
    if not root or not db_id:
        return UserRenderer(context=case.public.agent_payload())
    return UserRenderer.from_bird_root(str(root), str(db_id), context=case.public.agent_payload())
