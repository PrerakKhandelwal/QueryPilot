from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "QueryPilot"
    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    db_uri: str = "sqlite:///./demo.db"
    db_schema_name: str | None = None
    db_pool_size: int = 20
    db_max_overflow: int = 40
    db_pool_recycle_seconds: int = 1800
    db_connect_timeout_seconds: int = 10
    statement_timeout_ms: int = 15_000
    max_result_rows: int = 500
    demo_mode: bool = True

    llm_provider: Literal["ollama", "openai", "azure", "local"] = "ollama"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "llama3:latest"
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o-mini"
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_deployment: str = "gpt-4o-mini"
    local_text2sql_model_name: str = "defog/sqlcoder-7b-alpha"
    local_nl_model_name: str | None = None
    local_device: str = "cpu"
    max_new_tokens: int = 512
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 3

    cache_dir: str = "cache"
    faiss_index_path: str = "cache/faiss.index"
    metadata_path: str = "cache/metadata.json"
    similarity_threshold: float = 0.88
    cache_top_k: int = 4
    enable_semantic_cache: bool = False
    enable_exact_sql_cache: bool = True
    result_cache_ttl_seconds: int = 0

    max_schema_tables_in_prompt: int = 12
    max_question_chars: int = 2000
    max_sql_chars: int = 8000
    max_joins: int = 12
    max_subquery_depth: int = 6
    pipeline_retries: int = 2
    max_concurrent_queries: int = 32

    api_keys: str = ""
    rate_limit_per_minute: int = 60
    cors_origins: str = "*"

    @field_validator("db_schema_name", mode="before")
    @classmethod
    def normalize_empty_schema_name(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Backward-compatible aliases used by older scripts
class DBConfig:
    def __init__(self, uri: str, schema: str | None = None) -> None:
        self.uri = uri
        self.schema = schema


class AppConfig:
    def __init__(self, settings: Settings | None = None) -> None:
        s = settings or get_settings()
        self.db = DBConfig(uri=s.db_uri, schema=s.db_schema_name)
        self.models = s
        self.cache = s


def default_config() -> AppConfig:
    return AppConfig(get_settings())
