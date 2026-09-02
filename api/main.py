"""FastAPI surface: NL question in, SQL + rows + full trace out."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from agent.graph import build_graph
from agent.introspect import render_schema
from agent.llm import LlmClient
from agent.nodes import AgentDeps
from agent.state import initial_state
from api.config import get_settings
from api.database import db
from api.schemas import HealthResponse, QueryRequest, QueryResponse
from guardrails.validator import SqlValidator

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Built once at startup and closed over by the nodes.
_runtime: dict = {"graph": None, "schema": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    schema_ddl = render_schema(db)
    deps = AgentDeps(
        llm=LlmClient(
            api_key=settings.anthropic_api_key,
            model=settings.model,
            use_adaptive_thinking=settings.use_adaptive_thinking,
        ),
        schema_ddl=schema_ddl,
        db=db,
        validator=SqlValidator(max_rows=settings.max_rows),
        max_retries=settings.max_retries,
    )

    _runtime["graph"] = build_graph(deps)
    _runtime["schema"] = schema_ddl
    logger.info("agent ready; schema is %d chars", len(schema_ddl))

    yield

    db.dispose()


settings = get_settings()
app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    database_ok = db.healthcheck()
    return HealthResponse(
        ok=database_ok and _runtime["graph"] is not None,
        database=database_ok,
        schema_loaded=_runtime["schema"] is not None,
    )


@app.get("/schema")
def schema() -> dict:
    """The DDL the agent actually sees. Useful when a plan looks wrong."""
    return {"schema": _runtime["schema"] or ""}


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    graph = _runtime["graph"]
    if graph is None:
        raise HTTPException(status_code=503, detail="agent not initialised")

    # recursion_limit is a backstop only. The real bound is retry_count in the
    # repair node; if this limit is what stops a run, the loop guard is broken.
    final = graph.invoke(
        initial_state(request.question),
        config={"recursion_limit": 40},
    )

    execution = final.get("execution_result")
    status = "succeeded" if execution is not None else "failed"

    return QueryResponse(
        status=status,
        question=request.question,
        plan=final.get("plan"),
        sql=final.get("generated_sql"),
        executed_sql=final.get("safe_sql"),
        explanation=final.get("explanation"),
        result=execution,
        attempts=final.get("attempts", []),
        retry_count=final.get("retry_count", 0),
        failure_reason=final.get("failure_reason"),
    )
