from sql_agent.config import get_settings
from sql_agent.core.executor import create_db_engine as _create_engine
from sql_agent.core.schema import ColumnInfo, ForeignKeyInfo, SchemaSnapshot, introspect_schema


def create_db_engine(cfg) -> object:
    settings = get_settings().model_copy(update={"db_uri": cfg.uri, "db_schema_name": cfg.schema})
    return _create_engine(settings)


__all__ = [
    "ColumnInfo",
    "ForeignKeyInfo",
    "SchemaSnapshot",
    "introspect_schema",
    "create_db_engine",
]
