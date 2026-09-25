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
SYSTEM_PROMPT = """You are the Coding Orchestrator. Start and process code runs when required
specification artifact links are present, coordinate contract-first component generation, and
perform bounded component repairs. Never expose credentials."""
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
