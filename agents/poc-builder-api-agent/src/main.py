"""Standalone Magenta application for the API Agent."""

from __future__ import annotations

import os
from typing import Literal

from agent_engine_sdk_langgraph import App
from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from api_agent import APIAgent
from llm import build_llm
from runtime import AgentRuntime, build_runtime
from state import POCBuilderState
from tools.api import register

AGENT_NAME = "api_agent"
SYSTEM_PROMPT = """You are the API Agent. Use invoke_api_agent to generate an API contract,
backend implementation, or bounded repair. Persist all outputs through the shared artifact
store and return only AgentEnvelope-compatible results."""
os.environ.setdefault("RUNNER_MODE", "aer")
app = App(app_name="poc-builder-api-agent")
runtime = build_runtime()
service = APIAgent(runtime.repository, runtime.artifacts)
register(app, runtime, service)


def build_service(runtime: AgentRuntime) -> APIAgent:
    """Create an API Agent with shared persistent dependencies."""
    return APIAgent(runtime.repository, runtime.artifacts)


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    """Build the API Agent graph and generation tool."""
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
