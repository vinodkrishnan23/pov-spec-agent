"""Standalone Magenta application for the Coding Orchestrator."""

from __future__ import annotations

from typing import Literal

from agent_engine_sdk_langgraph import App
from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from agent_client import HTTPAgentClient
from llm import build_llm
from orchestrator import CodingOrchestrator
from runtime import AgentRuntime, build_runtime
from state import POCBuilderState
from tools.orchestrator import register

AGENT_NAME = "coding_orchestrator"
SYSTEM_PROMPT = """You are the Coding Orchestrator for POC Builder.

Use the available lifecycle tools to coordinate code generation and bounded
component repair for an existing POC. Do not generate application code yourself
and do not invoke child agents directly.

For a request to generate code:
1. Require a POC ID. If it is missing, ask the user for it.
2. Call start_code_run with the POC ID and requested specification version.
    This tool retrieves the POC and verifies that Git links exist for data_model,
    query_patterns, api_contract, frontend_contract, and requirements.
3. If start_code_run succeeds, call process_code_run with the returned run ID.
    It performs the fixed contract-first workflow: API contract generation,
    backend generation, then concurrent data-seeding and frontend generation.
4. Report the returned status, run ID, code version, and bundle or artifact
    references when present.

For a repair request:
1. Require a valid serialized failure report.
2. Call repair_component only for a failed seed, backend, or frontend component.
3. Report the returned repair status, code version, and generated artifact keys.

Treat tool results as authoritative. Never claim that validation, generation,
repair, or packaging succeeded unless the tool result explicitly reports
success. When a tool fails, explain the failed stage and returned error in
user-safe language, and state the next action needed.

Never expose credentials, tokens, connection strings, service-account secrets,
or internal configuration values."""
def build_service(runtime: AgentRuntime) -> CodingOrchestrator:
    """Create a Coding Orchestrator with local or remote child-agent adapters."""
    clients = {
        "api": (
            runtime.settings.api_agent_url,
            runtime.settings.api_agent_client_id,
            runtime.settings.api_agent_client_secret,
        ),
        "seed": (
            runtime.settings.data_seeding_agent_url,
            runtime.settings.data_seeding_agent_client_id,
            runtime.settings.data_seeding_agent_client_secret,
        ),
        "frontend": (
            runtime.settings.frontend_agent_url,
            runtime.settings.frontend_agent_client_id,
            runtime.settings.frontend_agent_client_secret,
        ),
    }
    missing = [name for name, values in clients.items() if not all(values)]
    if missing:
        raise RuntimeError(
            "Missing remote Agent Engine URL or service-account credentials: "
            + ", ".join(missing)
        )
    return CodingOrchestrator(
        runtime.repository,
        runtime.artifacts,
        HTTPAgentClient(*clients["api"]),
        runtime.settings.presigned_url_ttl_seconds,
        data_seeding_agent=HTTPAgentClient(*clients["seed"]),
        frontend_agent=HTTPAgentClient(*clients["frontend"]),
    )


app = App(app_name="poc-builder-coding-orchestrator")
runtime = build_runtime()
register(app, runtime, factory=build_service)


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    """Build the Coding Orchestrator graph and durable run tools."""
    runtime_llm = app.llm(build_llm())
    tools = app.get_tools()
    llm_with_tools = runtime_llm.bind_tools(app.get_tool_schemas())

    def agent_node(state: POCBuilderState) -> dict[str, object]:
        response = app.validate_llm_response(
            llm_with_tools.invoke(
                [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
            )
        )
        return {"messages": [response]}

    def route(state: POCBuilderState) -> Literal["tools", "end"]:
        return "tools" if getattr(state["messages"][-1], "tool_calls", None) else "end"

    builder = StateGraph(POCBuilderState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route, {"tools": "tools", "end": END})
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=app.checkpointer())


def main() -> None:
    app.run()
