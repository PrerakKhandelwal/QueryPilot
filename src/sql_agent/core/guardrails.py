from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError

from sql_agent.core.errors import GuardrailError

_MULTI_STMT = re.compile(r";\s*\S")
_NULL_BYTES = re.compile(r"[\x00]")
_COMMENT_SMUGGLE = re.compile(r"/\*!|xp_cmdshell|into\s+(outfile|dumpfile)", re.I)

FORBIDDEN_FUNCTION_NAMES = frozenset(
    {
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "pg_sleep",
        "pg_terminate_backend",
        "pg_reload_conf",
        "dblink",
        "dblink_exec",
        "dblink_connect",
        "lo_import",
        "lo_export",
        "lo_unlink",
        "load_file",
        "sleep",
        "benchmark",
        "updatexml",
        "extractvalue",
        "xp_cmdshell",
        "sp_configure",
        "openrowset",
        "opendatasource",
        "file_read",
        "sqlite_compileoption_used",
    }
)

FORBIDDEN_SCHEMA_PREFIXES = (
    "pg_catalog",
    "information_schema",
    "mysql",
    "sys",
    "msdb",
    "master",
    "performance_schema",
)

WRITE_EXPR_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Merge,
    exp.Command,
    exp.Grant,
    exp.Pragma,
)


@dataclass(frozen=True)
class GuardrailResult:
    sql: str
    tables: tuple[str, ...]
    dialect: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _normalize_ident(name: str | None) -> str:
    if not name:
        return ""
    if not isinstance(name, str):
        name = getattr(name, "this", name)
    name = str(name)
    return name.strip().strip('"').strip("`").strip("[]").lower()


def _table_name(node: exp.Table) -> str:
    catalog = _normalize_ident(node.catalog)
    db = _normalize_ident(node.db)
    name = _normalize_ident(node.name)
    parts = [p for p in (catalog, db, name) if p]
    return ".".join(parts)


def _is_read_query(expression: exp.Expression) -> bool:
    root = expression
    if isinstance(root, exp.With):
        root = root.this
    return isinstance(root, (exp.Select, exp.Union, exp.Except, exp.Intersect))


def _max_depth(expression: exp.Expression) -> int:
    depth = 0

    def walk(node: exp.Expression, current: int) -> None:
        nonlocal depth
        depth = max(depth, current)
        for child in node.iter_expressions():
            nxt = current + 1 if isinstance(child, exp.Subquery) else current
            walk(child, nxt)

    walk(expression, 0)
    return depth


def _alias_allowlist(expression: exp.Expression) -> set[str]:
    names: set[str] = set()
    for cte in expression.find_all(exp.CTE):
        alias = cte.args.get("alias")
        if alias is not None:
            names.add(_normalize_ident(getattr(alias, "this", None) or str(alias)))
        names.add(_normalize_ident(cte.alias))
    for sub in expression.find_all(exp.Subquery):
        names.add(_normalize_ident(sub.alias))
    return {n for n in names if n}


def _collect_tables(expression: exp.Expression) -> list[str]:
    names: list[str] = []
    for table in expression.find_all(exp.Table):
        names.append(_table_name(table))
    return names


def _assert_no_writes(expression: exp.Expression) -> None:
    for node in expression.walk():
        if isinstance(node, WRITE_EXPR_TYPES):
            raise GuardrailError(f"Write or DDL statement is not allowed: {type(node).__name__}")
        if isinstance(node, exp.Select) and node.args.get("into"):
            raise GuardrailError("SELECT INTO is not allowed.")
        if getattr(node, "args", None) and node.args.get("for_update"):
            raise GuardrailError("FOR UPDATE / locking reads are not allowed.")
        kind = getattr(node, "args", {}).get("kind")
        if isinstance(node, exp.Command) or (isinstance(kind, str) and kind.upper() in {"TRUNCATE", "COPY", "CALL", "EXEC", "EXECUTE"}):
            raise GuardrailError("Administrative or mutating commands are not allowed.")


def _assert_functions(expression: exp.Expression) -> None:
    for func in expression.find_all(exp.Func):
        name = _normalize_ident(func.sql_name() if hasattr(func, "sql_name") else func.name)
        if name in FORBIDDEN_FUNCTION_NAMES:
            raise GuardrailError(f"Function '{name}' is not allowed.")
        if name.startswith("pg_") and name not in {"pg_typeof"}:
            raise GuardrailError(f"Catalog function '{name}' is not allowed.")


def _assert_tables_allowed(
    tables: Iterable[str],
    allowed: set[str],
    aliases: set[str],
) -> None:
    allowed_norm = {_normalize_ident(t) for t in allowed}
    allowed_short = {t.split(".")[-1] for t in allowed_norm}
    alias_norm = {_normalize_ident(a) for a in aliases}
    for table in tables:
        t = _normalize_ident(table)
        if not t:
            continue
        short = t.split(".")[-1]
        prefix = t.split(".")[0]
        if short in alias_norm or t in alias_norm:
            continue
        if prefix in FORBIDDEN_SCHEMA_PREFIXES or t.startswith(FORBIDDEN_SCHEMA_PREFIXES):
            raise GuardrailError(f"System catalog access is not allowed: {table}")
        if short in {"sqlite_master", "sqlite_temp_master"}:
            raise GuardrailError("SQLite catalog access is not allowed.")
        if t not in allowed_norm and short not in allowed_short:
            raise GuardrailError(f"Table '{table}' is not in the allowed schema.")


def _apply_limit(expression: exp.Expression, max_rows: int, dialect: str) -> exp.Expression:
    root = expression
    target = root.this if isinstance(root, exp.With) else root
    if isinstance(target, (exp.Union, exp.Except, exp.Intersect)):
        wrapped = exp.Subquery(this=target.copy(), alias="guarded")
        limited = exp.Select(expressions=[exp.Star()]).from_(wrapped)
        limited.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        return limited
    if isinstance(target, exp.Select):
        existing = target.args.get("limit")
        if existing:
            try:
                n = int(existing.expression.this)
                if n > max_rows:
                    target.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
            except Exception:
                target.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        else:
            target.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        return root
    raise GuardrailError("Unable to enforce a row limit on this query.")


def sanitize_and_validate_sql(
    sql: str,
    *,
    dialect: str,
    allowed_tables: Iterable[str],
    max_rows: int,
    max_sql_chars: int,
    max_joins: int,
    max_subquery_depth: int,
) -> GuardrailResult:
    if not sql or not sql.strip():
        raise GuardrailError("Empty SQL is not allowed.")
    if _NULL_BYTES.search(sql):
        raise GuardrailError("SQL contains invalid control characters.")
    if len(sql) > max_sql_chars:
        raise GuardrailError("Generated SQL exceeds the maximum allowed length.")
    if _COMMENT_SMUGGLE.search(sql):
        raise GuardrailError("SQL contains a blocked construct.")

    stripped = sql.strip().rstrip(";").strip()
    if _MULTI_STMT.search(sql.strip()):
        raise GuardrailError("Multiple SQL statements are not allowed.")

    try:
        statements = sqlglot.parse(stripped, dialect=dialect)
    except (ParseError, SqlglotError) as exc:
        raise GuardrailError(f"SQL could not be parsed safely: {exc}") from exc

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise GuardrailError("Exactly one SQL statement is required.")

    expression = statements[0]
    if not _is_read_query(expression):
        raise GuardrailError("Only read-only SELECT queries are allowed.")

    _assert_no_writes(expression)
    _assert_functions(expression)

    join_count = sum(1 for _ in expression.find_all(exp.Join))
    if join_count > max_joins:
        raise GuardrailError(f"Query exceeds join limit ({max_joins}).")
    if _max_depth(expression) > max_subquery_depth:
        raise GuardrailError(f"Query exceeds subquery depth limit ({max_subquery_depth}).")

    tables = tuple(dict.fromkeys(_collect_tables(expression)))
    _assert_tables_allowed(tables, set(allowed_tables), _alias_allowlist(expression))

    limited = _apply_limit(expression, max_rows=max_rows, dialect=dialect)
    rendered = limited.sql(dialect=dialect, pretty=False)
    if ";" in rendered:
        raise GuardrailError("Sanitized SQL still contained a statement separator.")

    warnings: list[str] = []
    if join_count >= max(4, max_joins // 2):
        warnings.append("Query uses a high number of joins.")

    return GuardrailResult(sql=rendered, tables=tables, dialect=dialect, warnings=tuple(warnings))


def validate_question(question: str, max_chars: int) -> str:
    q = (question or "").strip()
    if not q:
        raise GuardrailError("Question cannot be empty.")
    if len(q) > max_chars:
        raise GuardrailError(f"Question exceeds {max_chars} characters.")
    if _NULL_BYTES.search(q):
        raise GuardrailError("Question contains invalid characters.")
    return q
