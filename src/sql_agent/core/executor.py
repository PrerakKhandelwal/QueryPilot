from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from sql_agent.config import Settings
from sql_agent.core.errors import ExecutionError


def detect_dialect(engine: Engine) -> str:
    name = engine.dialect.name
    mapping = {
        "postgresql": "postgres",
        "postgres": "postgres",
        "mysql": "mysql",
        "mariadb": "mysql",
        "mssql": "tsql",
        "sqlite": "sqlite",
        "snowflake": "snowflake",
        "bigquery": "bigquery",
    }
    return mapping.get(name, "postgres")


def create_db_engine(settings: Settings, *, writable: bool = False) -> Engine:
    connect_args: dict[str, Any] = {}
    engine_kwargs: dict[str, Any] = {
        "pool_pre_ping": True,
        "pool_recycle": settings.db_pool_recycle_seconds,
    }
    if settings.db_uri.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    else:
        engine_kwargs["pool_size"] = settings.db_pool_size
        engine_kwargs["max_overflow"] = settings.db_max_overflow
        engine_kwargs["pool_timeout"] = settings.db_connect_timeout_seconds

    engine = create_engine(settings.db_uri, connect_args=connect_args, **engine_kwargs)

    if not writable:

        @event.listens_for(engine, "connect")
        def _on_connect(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
            dialect = engine.dialect.name
            try:
                if dialect == "postgresql":
                    with dbapi_conn.cursor() as cur:
                        cur.execute(f"SET statement_timeout = {int(settings.statement_timeout_ms)}")
                elif dialect == "mysql":
                    with dbapi_conn.cursor() as cur:
                        cur.execute(
                            f"SET SESSION MAX_EXECUTION_TIME = {int(settings.statement_timeout_ms)}"
                        )
            except Exception:
                pass

    return engine


def execute_readonly(
    engine: Engine,
    sql: str,
    *,
    max_rows: int,
    timeout_ms: int,
) -> tuple[list[str], list[tuple[Any, ...]]]:
    dialect = engine.dialect.name
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                if dialect == "postgresql":
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                    conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
                elif dialect == "mysql":
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                elif dialect == "mssql":
                    conn.execute(text(f"SET LOCK_TIMEOUT {int(timeout_ms)}"))
                result = conn.execute(text(sql))
                rows = result.fetchmany(max_rows)
                columns = list(result.keys())
                trans.commit()
            except Exception:
                trans.rollback()
                raise
        return columns, [tuple(r) for r in rows]
    except SQLAlchemyError as exc:
        raise ExecutionError(f"Query execution failed: {exc}") from exc
    except Exception as exc:
        raise ExecutionError(str(exc)) from exc
