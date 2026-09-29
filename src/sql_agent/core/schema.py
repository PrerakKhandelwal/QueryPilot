from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from sql_agent.core.errors import SchemaError


@dataclass
class ColumnInfo:
    table: str
    name: str
    type: str
    nullable: bool


@dataclass
class ForeignKeyInfo:
    table: str
    column: str
    referred_table: str
    referred_column: str


@dataclass
class SchemaSnapshot:
    tables: list[str]
    columns: list[ColumnInfo]
    foreign_keys: list[ForeignKeyInfo]
    dialect: str = "postgres"

    def table_set(self) -> set[str]:
        return {t.lower() for t in self.tables}

    def to_prompt_string(self, include_tables: list[str] | None = None) -> str:
        wanted = {t.lower() for t in (include_tables or self.tables)}
        lines: list[str] = ["Database schema overview:"]
        table_to_cols: dict[str, list[ColumnInfo]] = {}
        for col in self.columns:
            table_to_cols.setdefault(col.table, []).append(col)

        for table in sorted(self.tables):
            if table.lower() not in wanted:
                continue
            cols = table_to_cols.get(table, [])
            col_str = ", ".join(f"{c.name} ({c.type})" for c in cols)
            lines.append(f"- Table {table}: {col_str}")

        fks = [
            fk
            for fk in self.foreign_keys
            if fk.table.lower() in wanted or fk.referred_table.lower() in wanted
        ]
        if fks:
            lines.append("Foreign key relationships:")
            for fk in fks:
                lines.append(
                    f"- {fk.table}.{fk.column} -> {fk.referred_table}.{fk.referred_column}"
                )
        return "\n".join(lines)

    def neighbor_tables(self, tables: list[str]) -> list[str]:
        selected = {t.lower() for t in tables}
        for fk in self.foreign_keys:
            if fk.table.lower() in selected:
                selected.add(fk.referred_table.lower())
            if fk.referred_table.lower() in selected:
                selected.add(fk.table.lower())
        original = {t.lower(): t for t in self.tables}
        return [original[t] for t in selected if t in original]


def introspect_schema(
    engine: Engine,
    default_schema: str | None = None,
    dialect: str = "postgres",
) -> SchemaSnapshot:
    try:
        inspector = inspect(engine)
        tables: list[str] = []
        columns: list[ColumnInfo] = []
        foreign_keys: list[ForeignKeyInfo] = []
        schema = default_schema
        if engine.dialect.name == "postgresql" and schema is None:
            schema = "public"

        for table_name in inspector.get_table_names(schema=schema):
            tables.append(table_name)
            for col in inspector.get_columns(table_name, schema=schema):
                columns.append(
                    ColumnInfo(
                        table=table_name,
                        name=col["name"],
                        type=str(col["type"]),
                        nullable=bool(col.get("nullable", True)),
                    )
                )
            for fk in inspector.get_foreign_keys(table_name, schema=schema):
                referred_table = fk.get("referred_table")
                referred_columns = fk.get("referred_columns") or []
                constrained_columns = fk.get("constrained_columns") or []
                for src, dst in zip(constrained_columns, referred_columns):
                    foreign_keys.append(
                        ForeignKeyInfo(
                            table=table_name,
                            column=src,
                            referred_table=referred_table,
                            referred_column=dst,
                        )
                    )
        if not tables:
            raise SchemaError("No tables found. Check DB URI and schema name.")
        return SchemaSnapshot(
            tables=tables,
            columns=columns,
            foreign_keys=foreign_keys,
            dialect=dialect,
        )
    except SchemaError:
        raise
    except Exception as exc:
        raise SchemaError(f"Failed to introspect schema: {exc}") from exc


_TOKEN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]+")


def select_relevant_tables(
    schema: SchemaSnapshot,
    question: str,
    max_tables: int,
) -> list[str]:
    if len(schema.tables) <= max_tables:
        return list(schema.tables)

    q = question.lower()
    tokens = {t.lower() for t in _TOKEN.findall(question) if len(t) > 2}
    scores: list[tuple[float, str]] = []
    cols_by_table: dict[str, list[str]] = {}
    for col in schema.columns:
        cols_by_table.setdefault(col.table, []).append(col.name.lower())

    for table in schema.tables:
        t = table.lower()
        score = 0.0
        if t in q or t.rstrip("s") in tokens or t in tokens:
            score += 5.0
        for tok in tokens:
            if tok in t or t in tok:
                score += 2.0
        for col in cols_by_table.get(table, []):
            if col in tokens:
                score += 1.5
            if col in q:
                score += 0.5
        scores.append((score, table))

    scores.sort(key=lambda x: (-x[0], x[1]))
    picked = [t for s, t in scores if s > 0][: max(1, max_tables - 2)]
    if not picked:
        picked = [t for _, t in scores[: max_tables]]
    expanded = schema.neighbor_tables(picked)
    if len(expanded) > max_tables:
        # Keep original picks first, then neighbors.
        ordered = list(dict.fromkeys(picked + expanded))
        expanded = ordered[:max_tables]
    return expanded
