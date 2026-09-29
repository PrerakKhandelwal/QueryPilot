from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy.engine import Engine, URL, make_url

from sql_agent.config import Settings
from sql_agent.core.executor import create_db_engine
from sql_agent.core.pipeline import NL2SQLAgent


MAX_UPLOAD_BYTES = 100 * 1024 * 1024


@dataclass
class ActiveConnection:
    label: str
    agent: NL2SQLAgent
    engine: Engine
    uploaded_path: Path | None = None


def _agent_settings(settings: Settings, db_uri: str) -> Settings:
    return settings.model_copy(
        update={
            "db_uri": db_uri,
            "demo_mode": False,
            "enable_exact_sql_cache": False,
            "enable_semantic_cache": False,
        }
    )


def readonly_sqlite_url(database_path: Path) -> str:
    absolute_path = database_path.resolve().as_posix()
    url = URL.create(
        "sqlite",
        database=f"file:{absolute_path}",
        query={"mode": "ro", "uri": "true"},
    )
    return url.render_as_string(hide_password=False)


def _connect(settings: Settings, db_uri: str, label: str, uploaded_path: Path | None = None) -> ActiveConnection:
    user_settings = _agent_settings(settings, db_uri)
    engine = create_db_engine(user_settings, writable=False)
    try:
        agent = NL2SQLAgent(user_settings, engine=engine)
        if not agent.schema.tables:
            raise ValueError("The selected database has no visible tables.")
        return ActiveConnection(label=label, agent=agent, engine=engine, uploaded_path=uploaded_path)
    except Exception:
        engine.dispose()
        if uploaded_path:
            uploaded_path.unlink(missing_ok=True)
        raise


def connect_to_database_url(settings: Settings, database_url: str) -> ActiveConnection:
    try:
        url = make_url(database_url.strip())
    except Exception as exc:
        raise ValueError("Enter a valid SQLAlchemy database URL.") from exc

    backend = url.get_backend_name()
    if backend == "sqlite":
        if not url.database or url.database == ":memory:":
            raise ValueError("Use a file-based SQLite database URL, not an in-memory URL.")
        path = Path(url.database)
        if not path.is_file():
            raise ValueError("The SQLite database file was not found.")
        db_uri = readonly_sqlite_url(path)
    elif backend == "postgresql":
        db_uri = url.render_as_string(hide_password=False)
    else:
        raise ValueError("Supported database URLs: SQLite file URLs and PostgreSQL URLs.")

    return _connect(settings, db_uri, label=f"{backend.title()} database")


def connect_to_uploaded_sqlite(settings: Settings, filename: str, content: bytes) -> ActiveConnection:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("Database uploads must be 100 MB or smaller.")
    if not filename.lower().endswith((".db", ".sqlite", ".sqlite3")):
        raise ValueError("Upload a SQLite file ending in .db, .sqlite, or .sqlite3.")
    if not content.startswith(b"SQLite format 3\x00"):
        raise ValueError("The uploaded file does not appear to be a valid SQLite database.")

    temp_file = NamedTemporaryFile(prefix="sql-agent-", suffix=".db", delete=False)
    uploaded_path = Path(temp_file.name)
    try:
        with temp_file:
            temp_file.write(content)
        label = Path(filename).name
        return _connect(
            settings,
            readonly_sqlite_url(uploaded_path),
            label=label,
            uploaded_path=uploaded_path,
        )
    except Exception:
        uploaded_path.unlink(missing_ok=True)
        raise


def close_connection(connection: ActiveConnection | None) -> None:
    if connection is None:
        return
    connection.engine.dispose()
    if connection.uploaded_path:
        connection.uploaded_path.unlink(missing_ok=True)
