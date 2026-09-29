from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sql_agent.config import Settings, get_settings
from sql_agent.core.bootstrap import ensure_demo_database
from sql_agent.core.errors import AuthError, ExecutionError, GuardrailError, LLMError, RateLimitError, SchemaError
from sql_agent.core.observability import configure_logging
from sql_agent.core.pipeline import NL2SQLAgent


class AnalyzeRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


class AnalyzeResponse(BaseModel):
    request_id: str
    question: str
    sql: str
    answer: str
    columns: list[str]
    rows: list[list[Any]]
    tables_used: list[str]
    cached_sql: bool
    retries: int
    warnings: list[str]


def _agent(request: Request) -> NL2SQLAgent:
    return request.app.state.agent


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> str:
    settings: Settings = request.app.state.settings
    keys = settings.api_key_set()
    if settings.environment != "production" and not keys:
        return "anonymous"
    offered = x_api_key
    if not offered and authorization and authorization.lower().startswith("bearer "):
        offered = authorization.split(" ", 1)[1]
    if not offered or offered not in keys:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
    return offered


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = ensure_demo_database(settings)
    app.state.settings = settings
    app.state.agent = NL2SQLAgent(settings, engine=engine)
    yield
    engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="QueryPilot API",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready(request: Request) -> dict[str, Any]:
        agent: NL2SQLAgent = request.app.state.agent
        return {
            "status": "ok",
            "tables": len(agent.schema.tables),
            "dialect": agent.dialect,
        }

    @app.post("/v1/analyze", response_model=AnalyzeResponse)
    def analyze(
        payload: AnalyzeRequest,
        request: Request,
        client_id: str = Depends(require_api_key),
        agent: NL2SQLAgent = Depends(_agent),
    ) -> AnalyzeResponse:
        try:
            result = agent.analyze(payload.question, client_key=client_id)
        except RateLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except GuardrailError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (ExecutionError, SchemaError, LLMError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return AnalyzeResponse(
            request_id=result.request_id,
            question=result.question,
            sql=result.sql,
            answer=result.answer,
            columns=result.columns,
            rows=[list(r) for r in result.rows],
            tables_used=result.tables_used,
            cached_sql=result.cached_sql,
            retries=result.retries,
            warnings=result.warnings,
        )

    return app


app = create_app()
