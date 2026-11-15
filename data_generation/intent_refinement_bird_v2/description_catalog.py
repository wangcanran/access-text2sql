"""Business descriptions from BIRD ``database_description`` CSV files.

The catalog is an internal lookup.  It never changes schema bindings and it
does not get sent to the parser as a replacement for the real schema.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


@dataclass(frozen=True)
class ColumnDescription:
    db_id: str
    table: str
    column: str
    label: str
    description: str = ""
    data_format: str = ""
    value_description: str = ""

    @property
    def qualified_name(self) -> str:
        return f"{self.table}.{self.column}"


def _clean(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _humanize(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value.replace("_", " ").replace("-", " "))
    return " ".join(value.split()).lower()


def _safe_label(table: str, column: str, row: Mapping[str, str]) -> str:
    raw_column = _clean(row.get("original_column_name", "")) or column
    candidate = _clean(row.get("column_description", "")) or _clean(row.get("column_name", ""))
    # A description that merely repeats the physical identifier is not a
    # business label.  Humanizing it avoids exposing snake_case/CamelCase.
    if not candidate or candidate.casefold() in {column.casefold(), raw_column.casefold(), f"{table}.{column}".casefold()}:
        # A missing/identifier-only description must not become a schema
        # leak.  The renderer can still provide a useful generic question.
        candidate = "the requested field"
    return candidate


class DescriptionCatalog:
    def __init__(self, entries: Iterable[ColumnDescription] = ()):
        self._entries = {(item.db_id, item.table, item.column): item for item in entries}

    def get(self, table: str, column: str, *, db_id: str | None = None) -> ColumnDescription | None:
        matches = [item for (db, tab, col), item in self._entries.items() if tab == table and col == column and (db_id is None or db == db_id)]
        return matches[0] if matches else None

    def label(self, table: str, column: str, *, db_id: str | None = None) -> str:
        item = self.get(table, column, db_id=db_id)
        return item.label if item else "the requested field"

    def entries(self) -> tuple[ColumnDescription, ...]:
        return tuple(self._entries.values())

    @classmethod
    def from_database(cls, database_dir: str | Path, *, db_id: str | None = None) -> "DescriptionCatalog":
        directory = Path(database_dir)
        resolved_db = db_id or directory.name
        description_dir = directory / "database_description"
        entries: list[ColumnDescription] = []
        for path in sorted(description_dir.glob("*.csv")):
            table = path.stem
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    column = _clean(row.get("original_column_name"))
                    if not column:
                        continue
                    entries.append(ColumnDescription(
                        resolved_db, table, column, _safe_label(table, column, row),
                        _clean(row.get("column_description")), _clean(row.get("data_format")),
                        _clean(row.get("value_description")),
                    ))
        return cls(entries)

    @classmethod
    def from_root(cls, root: str | Path, db_id: str) -> "DescriptionCatalog":
        return cls.from_database(Path(root) / db_id, db_id=db_id)


def load_bird_description_catalog(root: str | Path, db_id: str) -> DescriptionCatalog:
    return DescriptionCatalog.from_root(root, db_id)
