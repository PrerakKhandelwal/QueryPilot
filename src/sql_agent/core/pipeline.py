from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import Engine

from sql_agent.config import Settings, get_settings
from sql_agent.core.cache import QueryCache, TTLResultCache
from sql_agent.core.errors import ExecutionError, GuardrailError, RateLimitError
from sql_agent.core.executor import create_db_engine, detect_dialect, execute_readonly
from sql_agent.core.guardrails import sanitize_and_validate_sql, validate_question
from sql_agent.core.llm import LLMProvider, build_llm
from sql_agent.core.nl_answer import NLAnswerGenerator
from sql_agent.core.observability import log_event, new_request_id, timed
from sql_agent.core.rate_limit import ConcurrencyLimiter, SlidingWindowRateLimiter
from sql_agent.core.schema import SchemaSnapshot, introspect_schema, select_relevant_tables
from sql_agent.core.text2sql import Text2SQLGenerator


@dataclass
class AnalysisResult:
    request_id: str
    question: str
    sql: str
    answer: str
    columns: list[str]
    rows: list[tuple[Any, ...]]
    tables_used: list[str]
    cached_sql: bool = False
    retries: int = 0
    warnings: list[str] = field(default_factory=list)


class NL2SQLAgent:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        engine: Engine | None = None,
        schema: SchemaSnapshot | None = None,
        sql_llm: LLMProvider | None = None,
        nl_llm: LLMProvider | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.engine = engine or create_db_engine(self.settings)
        self.dialect = detect_dialect(self.engine)
        self.schema = schema or introspect_schema(
            self.engine,
            default_schema=self.settings.db_schema_name,
            dialect=self.dialect,
        )
        sql_provider = sql_llm or build_llm(self.settings)
        nl_provider = nl_llm or sql_provider
        if nl_llm is None and self.settings.llm_provider == "local" and self.settings.local_nl_model_name:
            nl_provider = build_llm(self.settings, for_nl=True)
        self.text2sql = Text2SQLGenerator(
            sql_provider, dialect=self.dialect, max_tokens=self.settings.max_new_tokens
        )
        self.answer_gen = NLAnswerGenerator(nl_provider, max_tokens=self.settings.max_new_tokens)
        self.cache = QueryCache(self.settings)
        self.result_cache = TTLResultCache(self.settings.result_cache_ttl_seconds)
        self.rate_limiter = SlidingWindowRateLimiter(self.settings.rate_limit_per_minute)
        self.concurrency = ConcurrencyLimiter(self.settings.max_concurrent_queries)

    def refresh_schema(self) -> None:
        self.schema = introspect_schema(
            self.engine,
            default_schema=self.settings.db_schema_name,
            dialect=self.dialect,
        )

    def analyze(self, question: str, *, client_key: str = "local") -> AnalysisResult:
        request_id = new_request_id()
        self.rate_limiter.check(client_key)
        if not self.concurrency.acquire():
            raise RateLimitError("Server is at capacity. Retry later.")
        try:
            return self._analyze(question, request_id)
        finally:
            self.concurrency.release()

    def _analyze(self, question: str, request_id: str) -> AnalysisResult:
        q = validate_question(question, self.settings.max_question_chars)
        log_event("analyze.start", request_id=request_id, question_len=len(q))

        tables = select_relevant_tables(
            self.schema, q, self.settings.max_schema_tables_in_prompt
        )
        similar = [ex for ex, _ in self.cache.similar_examples(q)]
        cached_sql = self.cache.exact_sql(q)
        sql = cached_sql
        retries = 0
        used_cache = bool(cached_sql)
        last_error: str | None = None
        guarded = None

        if sql:
            try:
                guarded = self._guard(sql)
            except GuardrailError:
                sql = None
                used_cache = False

        if not sql:
            attempts = self.settings.pipeline_retries + 1
            for attempt in range(attempts):
                retries = attempt
                with timed("llm.sql", request_id, attempt=attempt):
                    candidate = self.text2sql.generate(
                        q,
                        self.schema,
                        few_shot_examples=similar,
                        schema_tables=tables,
                        repair_hint=last_error,
                    )
                try:
                    guarded = self._guard(candidate)
                    sql = guarded.sql
                    last_error = None
                    break
                except GuardrailError as exc:
                    last_error = str(exc)
                    sql = None
            if not guarded or not sql:
                raise GuardrailError(last_error or "Failed to produce a safe SQL query.")

        assert guarded is not None
        cached_rows = self.result_cache.get(guarded.sql)
        if cached_rows:
            columns, rows = cached_rows
        else:
            try:
                with timed("db.execute", request_id):
                    columns, rows = execute_readonly(
                        self.engine,
                        guarded.sql,
                        max_rows=self.settings.max_result_rows,
                        timeout_ms=self.settings.statement_timeout_ms,
                    )
            except ExecutionError as exc:
                last_error = str(exc)
                used_cache = False
                candidate = self.text2sql.generate(
                    q,
                    self.schema,
                    few_shot_examples=similar,
                    schema_tables=tables,
                    repair_hint=last_error,
                )
                guarded = self._guard(candidate)
                retries += 1
                with timed("db.execute.retry", request_id):
                    columns, rows = execute_readonly(
                        self.engine,
                        guarded.sql,
                        max_rows=self.settings.max_result_rows,
                        timeout_ms=self.settings.statement_timeout_ms,
                    )
            self.result_cache.set(guarded.sql, columns, rows)

        with timed("llm.answer", request_id):
            answer = self.answer_gen.generate(q, guarded.sql, columns, rows)

        if not used_cache and (
            self.settings.enable_exact_sql_cache or self.settings.enable_semantic_cache
        ):
            self.cache.add_example(q, guarded.sql)

        result = AnalysisResult(
            request_id=request_id,
            question=q,
            sql=guarded.sql,
            answer=answer,
            columns=columns,
            rows=rows,
            tables_used=list(guarded.tables),
            cached_sql=used_cache,
            retries=retries,
            warnings=list(guarded.warnings),
        )
        log_event(
            "analyze.complete",
            request_id=request_id,
            cached_sql=used_cache,
            retries=retries,
            row_count=len(rows),
        )
        return result

    def _guard(self, sql: str):
        return sanitize_and_validate_sql(
            sql,
            dialect=self.dialect,
            allowed_tables=self.schema.table_set(),
            max_rows=self.settings.max_result_rows,
            max_sql_chars=self.settings.max_sql_chars,
            max_joins=self.settings.max_joins,
            max_subquery_depth=self.settings.max_subquery_depth,
        )
