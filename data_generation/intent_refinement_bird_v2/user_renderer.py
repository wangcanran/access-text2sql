"""User-side rendering boundary.

Internal graph objects may contain physical schema identifiers.  This module
is the only place used to turn them into text shown to a user.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Mapping

from .description_catalog import DescriptionCatalog, _humanize, load_bird_description_catalog


class UserRenderer:
    def __init__(self, catalog: DescriptionCatalog | None = None, *, db_id: str | None = None, context: Mapping[str, Any] | None = None):
        self.catalog = catalog or DescriptionCatalog()
        self.db_id = db_id
        self.context = context or {}

    def field(self, table: str, column: str) -> str:
        return self.catalog.label(table, column, db_id=self.db_id)

    def table(self, table: str) -> str:
        # BIRD descriptions are column-level.  Do not echo a physical table
        # identifier when no business-level table label exists.
        return "the relevant records"

    def binding(self, qualified_name: str) -> str:
        if "." not in qualified_name:
            return _humanize(qualified_name)
        table, column = qualified_name.split(".", 1)
        return self.field(table, column)

    def text(self, value: str) -> str:
        """Scrub physical identifiers from arbitrary agent-generated text."""
        result = str(value or "")
        names = []
        tables = self.context.get("schema", self.context).get("tables", {}) if isinstance(self.context.get("schema", self.context), Mapping) else {}
        for table, columns in tables.items() if isinstance(tables, Mapping) else ():
            for column in columns if isinstance(columns, (list, tuple, set)) else ():
                names.append((f"{table}.{column}", self.field(str(table), str(column))))
                names.append((str(column), self.field(str(table), str(column))))
            names.append((str(table), self.table(str(table))))
        for old, new in sorted(names, key=lambda item: len(item[0]), reverse=True):
            result = re.sub(rf"(?<![\w.]){re.escape(old)}(?![\w.])", new, result, flags=re.IGNORECASE)
        return " ".join(result.split())

    def business(self, value: str) -> str:
        """Render parser business prose without rewriting ordinary table words.

        ``text`` is intentionally defensive for arbitrary agent text and may
        replace a bare table identifier.  That is unsafe for generated prose:
        a legitimate subject such as ``bond records`` would become ``the
        relevant records records``.  Contract renderers therefore use this
        narrower boundary: qualified physical names and column identifiers
        are scrubbed, while ordinary business nouns are preserved.
        """
        result = str(value or "")
        schema = self.context.get("schema", self.context)
        tables = schema.get("tables", {}) if isinstance(schema, Mapping) else {}
        names = []
        for table, columns in tables.items() if isinstance(tables, Mapping) else ():
            for column in columns if isinstance(columns, (list, tuple, set)) else ():
                names.append((f"{table}.{column}", self.field(str(table), str(column))))
                # Only scrub identifier-like bare columns.  Common words such
                # as ``label`` or ``bond`` are left in business prose unless
                # the model supplied the fully qualified physical name.
                if "_" in str(column) or any(char.isupper() for char in str(column)):
                    names.append((str(column), self.field(str(table), str(column))))
        placeholders = []
        for index, (old, new) in enumerate(sorted(names, key=lambda item: len(item[0]), reverse=True)):
            marker = f"__BUSINESS_FIELD_{index}__"
            result = re.sub(rf"(?<![\w.]){re.escape(old)}(?![\w.])", marker, result, flags=re.IGNORECASE)
            placeholders.append((marker, new))
        for marker, new in placeholders:
            result = result.replace(marker, new)
        return " ".join(result.split())

    def node_reference(self, node: Any) -> str:
        bindings = [getattr(item, "qualified_name", "") for item in getattr(node, "schema_bindings", ()) if getattr(item, "valid", True)]
        if bindings:
            labels = [self.binding(item) for item in bindings]
            return " / ".join(dict.fromkeys(labels))
        return self.text(getattr(node, "business_meaning", ""))

    def question(self, value: str) -> str:
        return self.text(value)

    def alternative(self, value: str) -> str:
        return self.text(value)

    def action(self, response: Any) -> Any:
        """Render parser-generated user text before it crosses the boundary."""
        clarification = getattr(response, "clarification", None)
        alternative = getattr(response, "alternative", None)
        changes = {}
        if clarification is not None:
            changes["clarification"] = replace(clarification, question=self.question(clarification.question))
        if alternative is not None:
            changes["alternative"] = replace(alternative, summary=self.alternative(alternative.summary), rationale=self.alternative(alternative.rationale))
        return replace(response, **changes) if changes else response

    @classmethod
    def from_bird_root(cls, root: str, db_id: str, *, context: Mapping[str, Any] | None = None) -> "UserRenderer":
        """Load the BIRD descriptions for one database and build a renderer."""
        return cls(load_bird_description_catalog(root, db_id), db_id=db_id, context=context)


def renderer_for_context(context: Mapping[str, Any], *, bird_description_root: str | None = None) -> UserRenderer:
    """Construct the user renderer used by a dialogue runtime.

    ``bird_description_root`` points at the directory containing database
    folders such as ``.../dev_databases``.  Without it, rendering remains
    safe through the deterministic humanized-name fallback.
    """
    db_id = context.get("db_id")
    if db_id is None:
        metadata = context.get("metadata")
        if isinstance(metadata, Mapping):
            db_id = metadata.get("db_id")
    if bird_description_root and db_id:
        return UserRenderer.from_bird_root(bird_description_root, str(db_id), context=context)
    return UserRenderer(context=context)
