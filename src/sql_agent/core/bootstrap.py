from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from sql_agent.config import Settings
from sql_agent.core.executor import create_db_engine
from sql_agent.data.demo_schema import create_demo_schema_and_data


def ensure_demo_database(settings: Settings) -> Engine:
    """Create demo tables when DEMO_MODE is on and the catalog is empty."""
    engine = create_db_engine(settings, writable=True)
    if not settings.demo_mode:
        return engine
    if settings.db_uri.startswith("sqlite"):
        Path("demo.db").parent.mkdir(parents=True, exist_ok=True)
    try:
        from sqlalchemy import inspect

        inspector = inspect(engine)
        schema = settings.db_schema_name or None
        if engine.dialect.name == "postgresql" and schema is None:
            schema = "public"
        if inspector.get_table_names(schema=schema):
            return engine
        create_demo_schema_and_data(engine)
    except SQLAlchemyError:
        create_demo_schema_and_data(engine)
    return engine
