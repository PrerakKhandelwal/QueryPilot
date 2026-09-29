from __future__ import annotations

from pathlib import Path

from sql_agent.config import Settings
from sql_agent.core.bootstrap import ensure_demo_database
from sql_agent.core.executor import detect_dialect
from sql_agent.core.llm import LLMProvider
from sql_agent.core.pipeline import NL2SQLAgent
from sql_agent.core.schema import introspect_schema


class ScriptedLLM(LLMProvider):
    def __init__(self, sql_replies: list[str], answer: str = "North had the largest share of payments.") -> None:
        self.sql_replies = list(sql_replies)
        self.answer = answer

    def complete(self, prompt: str, *, max_tokens: int, stop: list[str] | None = None) -> str:
        if "natural-language answer" in prompt.lower():
            return self.answer
        if self.sql_replies:
            return self.sql_replies.pop(0)
        return "SELECT name FROM regions"


def _settings(tmp_path: Path) -> Settings:
    db = tmp_path / "demo.db"
    cache = tmp_path / "cache"
    cache.mkdir()
    return Settings(
        db_uri=f"sqlite:///{db.as_posix()}",
        demo_mode=True,
        llm_provider="openai",
        enable_semantic_cache=False,
        enable_exact_sql_cache=True,
        result_cache_ttl_seconds=0,
        cache_dir=str(cache),
        faiss_index_path=str(cache / "faiss.index"),
        metadata_path=str(cache / "metadata.json"),
        pipeline_retries=2,
        max_concurrent_queries=8,
        rate_limit_per_minute=100,
    )


def test_pipeline_happy_path(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    engine = ensure_demo_database(settings)
    schema = introspect_schema(engine, dialect=detect_dialect(engine))
    llm = ScriptedLLM(
        [
            "SELECT r.name, SUM(p.amount) AS total FROM payments p "
            "JOIN orders o ON o.id = p.order_id "
            "JOIN customers c ON c.id = o.customer_id "
            "JOIN regions r ON r.id = c.region_id "
            "GROUP BY r.name"
        ]
    )
    agent = NL2SQLAgent(settings, engine=engine, schema=schema, sql_llm=llm, nl_llm=llm)
    result = agent.analyze("Total payments by region?")
    assert result.rows
    assert result.answer
    assert "limit" in result.sql.lower()
    engine.dispose()


def test_pipeline_repairs_blocked_sql(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    engine = ensure_demo_database(settings)
    schema = introspect_schema(engine, dialect=detect_dialect(engine))
    llm = ScriptedLLM(
        [
            "DROP TABLE customers",
            "SELECT name FROM regions",
        ]
    )
    agent = NL2SQLAgent(settings, engine=engine, schema=schema, sql_llm=llm, nl_llm=llm)
    result = agent.analyze("List regions")
    assert "regions" in result.sql.lower()
    assert result.retries >= 1
    engine.dispose()
