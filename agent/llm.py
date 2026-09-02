"""Anthropic client wrapper for the two nodes that call a model.

Both calls use structured outputs (``client.messages.parse`` with a Pydantic
model) rather than asking for SQL in prose and regexing it back out. Parsing
model prose to find a code fence is the same category of mistake as parsing SQL
with a regex -- let the schema do it.

Note on refusal fallbacks: the Anthropic docs recommend passing the server-side
``fallbacks`` parameter on Opus-5-class models, but that lives on
``client.beta.messages`` while ``messages.parse`` lives on the stable client.
Since a text-to-SQL prompt over your own schema is a low-refusal workload, this
build keeps the typed parse helper and handles ``stop_reason == "refusal"``
explicitly instead. Swap to the beta endpoint if you start seeing declines.
"""

from __future__ import annotations

import logging

import anthropic
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MAX_TOKENS = 8000


class QueryPlan(BaseModel):
    """What the plan node produces. Deliberately not SQL."""

    steps: list[str] = Field(
        description="Ordered analysis steps: which tables, joins, filters, aggregations."
    )
    tables: list[str] = Field(description="Tables the query will need.")
    notes: str = Field(default="", description="Ambiguities or assumptions made.")

    def render(self) -> str:
        lines = [f"{i}. {s}" for i, s in enumerate(self.steps, 1)]
        out = "\n".join(lines)
        if self.tables:
            out += f"\n\nTables: {', '.join(self.tables)}"
        if self.notes:
            out += f"\n\nNotes: {self.notes}"
        return out


class GeneratedSql(BaseModel):
    """What the generate node produces."""

    sql: str = Field(description="A single read-only SELECT statement for PostgreSQL.")
    explanation: str = Field(
        default="", description="One or two sentences on what the query does."
    )


class LlmRefusal(RuntimeError):
    """The model declined the request. Distinct from an API error."""


class LlmClient:
    """Thin wrapper so the nodes never touch the SDK surface directly."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-5",
        use_adaptive_thinking: bool = True,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model
        self.use_adaptive_thinking = use_adaptive_thinking

    def _extra(self) -> dict:
        if not self.use_adaptive_thinking:
            return {}
        return {"thinking": {"type": "adaptive"}}

    def _parse(self, system: str, prompt: str, output_format: type[BaseModel]):
        response = self._client.messages.parse(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=output_format,
            **self._extra(),
        )
        # stop_details is populated only on a refusal; guard before reading it.
        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            raise LlmRefusal(f"model declined the request: {detail}")
        return response.parsed_output

    def plan(self, system: str, prompt: str) -> QueryPlan:
        return self._parse(system, prompt, QueryPlan)

    def generate(self, system: str, prompt: str) -> GeneratedSql:
        return self._parse(system, prompt, GeneratedSql)
