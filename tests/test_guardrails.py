from __future__ import annotations

import pytest

from sql_agent.core.errors import GuardrailError
from sql_agent.core.guardrails import sanitize_and_validate_sql, validate_question

TABLES = {"regions", "customers", "orders", "payments", "order_items", "products"}


def check(sql: str, dialect: str = "sqlite"):
    return sanitize_and_validate_sql(
        sql,
        dialect=dialect,
        allowed_tables=TABLES,
        max_rows=50,
        max_sql_chars=8000,
        max_joins=12,
        max_subquery_depth=6,
    )


def test_select_is_limited() -> None:
    result = check("SELECT name FROM regions")
    assert "limit" in result.sql.lower()
    assert "regions" in result.tables


def test_insert_blocked() -> None:
    with pytest.raises(GuardrailError):
        check("INSERT INTO regions (name) VALUES ('x')")


def test_drop_blocked() -> None:
    with pytest.raises(GuardrailError):
        check("DROP TABLE customers")


def test_multi_statement_blocked() -> None:
    with pytest.raises(GuardrailError):
        check("SELECT 1; DELETE FROM customers")


def test_unknown_table_blocked() -> None:
    with pytest.raises(GuardrailError):
        check("SELECT * FROM users")


def test_cte_allowed() -> None:
    result = check("WITH r AS (SELECT name FROM regions) SELECT name FROM r")
    assert result.sql


def test_system_catalog_blocked() -> None:
    with pytest.raises(GuardrailError):
        check("SELECT * FROM sqlite_master")


def test_question_validation() -> None:
    assert validate_question("  total sales  ", 100) == "total sales"
    with pytest.raises(GuardrailError):
        validate_question("", 100)
    with pytest.raises(GuardrailError):
        validate_question("x" * 50, 10)
