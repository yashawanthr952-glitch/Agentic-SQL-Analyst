"""Graph wiring.

    START -> plan -> generate -> validate --ok--> execute --ok--> END
                       ^           |                  |
                       |         fail              db_error
                       |           v                  |
                       +-------- repair <-------------+
                       |           |
                       |     out of retries
                       |           v
                       |          END (status="failed")
                       |
             (schema errors route repair -> plan instead of -> generate)

Every path that produces SQL goes through `validate` before `execute`. There is
deliberately no repair -> execute edge: adding one would bypass the guardrail
while leaving the graph looking correct.
"""

from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph

from agent.nodes import (
    AgentDeps,
    make_execute_node,
    make_generate_node,
    make_plan_node,
    make_repair_node,
    make_validate_node,
)
from agent.state import AgentState


def route_after_plan(state: AgentState) -> Literal["generate", "__end__"]:
    """A refusal or hard failure in plan ends the run; there is nothing to
    generate from."""
    if state.get("status") == "failed":
        return END
    return "generate"


def route_after_generate(state: AgentState) -> Literal["validate", "__end__"]:
    if state.get("status") == "failed":
        return END
    return "validate"


def route_after_validate(state: AgentState) -> Literal["execute", "repair"]:
    validation = state.get("validation_result") or {}
    return "execute" if validation.get("ok") else "repair"


def route_after_execute(state: AgentState) -> Literal["repair", "__end__"]:
    return "repair" if state.get("db_error") else END


def route_after_repair(state: AgentState) -> Literal["plan", "generate", "__end__"]:
    """Guards the loop. Returning to generate unconditionally would spin
    forever on any persistently bad query."""
    if state.get("status") == "failed":
        return END
    return "plan" if state.get("repair_target") == "plan" else "generate"


def build_graph(deps: AgentDeps):
    g = StateGraph(AgentState)

    g.add_node("plan", make_plan_node(deps))
    g.add_node("generate", make_generate_node(deps))
    g.add_node("validate", make_validate_node(deps))
    g.add_node("execute", make_execute_node(deps))
    g.add_node("repair", make_repair_node(deps))

    g.add_edge(START, "plan")

    g.add_conditional_edges("plan", route_after_plan, {"generate": "generate", END: END})
    g.add_conditional_edges(
        "generate", route_after_generate, {"validate": "validate", END: END}
    )
    g.add_conditional_edges(
        "validate", route_after_validate, {"execute": "execute", "repair": "repair"}
    )
    g.add_conditional_edges("execute", route_after_execute, {"repair": "repair", END: END})
    g.add_conditional_edges(
        "repair", route_after_repair, {"plan": "plan", "generate": "generate", END: END}
    )

    return g.compile()
