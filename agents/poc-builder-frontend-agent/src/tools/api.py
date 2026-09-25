"""Magenta HTTP tool exposed by the Frontend Agent."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_engine_sdk_langgraph import App

from agent import FrontendAgent
from runtime import AgentRuntime


def register(
    app: App,
    runtime: AgentRuntime,
    agent: FrontendAgent | None = None,
    factory: Callable[[AgentRuntime], FrontendAgent] | None = None,
) -> None:
    """Expose an AgentEnvelope endpoint for remote agent invocations."""

    def service() -> FrontendAgent:
        if agent is not None:
            return agent
        if factory is not None:
            return factory(runtime)
        return FrontendAgent(runtime.repository, runtime.artifacts)

    @app.tool(is_local=False)
    async def invoke_frontend_agent(envelope: dict[str, Any]) -> dict[str, Any]:
        """Generate or repair a React/Vite frontend from an AgentEnvelope."""
        return (await service().execute_payload(envelope)).to_dict()
