"""Standalone Magenta application and workflow for the Frontend Agent."""

from __future__ import annotations

import os
from typing import Literal

from agent_engine_sdk_langgraph import App
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from agent import FrontendAgent
from generation import generate_frontend
from llm import build_llm
from runtime import AgentRuntime, build_runtime
from state import POCBuilderState
from system_message import SYSTEM_PROMPT
from tools.api import register

AGENT_NAME = "frontend_agent"
load_dotenv()
os.environ.setdefault("RUNNER_MODE", "aer")
app = App(app_name="poc-builder-frontend-agent")
runtime = build_runtime()
register(app, runtime)


def build_component(
    title: str, user_stories: list[str], api_contract: str
) -> dict[str, str]:
    """Generate the React/Vite frontend component."""
    return generate_frontend(title, user_stories, api_contract)


def build_service(agent_runtime: AgentRuntime) -> FrontendAgent:
    """Create a Frontend Agent with shared persistent dependencies."""
    return FrontendAgent(agent_runtime.repository, agent_runtime.artifacts)


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    """Build the Frontend Agent tool-calling workflow."""
    runtime_llm = app.llm(build_llm())
    tools = app.get_tools()
    llm_with_tools = runtime_llm.bind_tools(app.get_tool_schemas())

    def agent_node(state: POCBuilderState) -> dict[str, object]:
        messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
        response = app.validate_llm_response(llm_with_tools.invoke(messages))
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


if __name__ == "__main__":
    main()
