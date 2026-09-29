"""Create demo schema/data. Uses DB_URI from environment / .env."""

from sql_agent.config import get_settings
from sql_agent.core.bootstrap import ensure_demo_database


def main() -> None:
    settings = get_settings()
    engine = ensure_demo_database(settings)
    print("Demo database is ready.")
    engine.dispose()


if __name__ == "__main__":
    main()
