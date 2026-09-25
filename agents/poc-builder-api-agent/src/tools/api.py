"""Magenta HTTP tool exposed by the API Agent."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_engine_sdk_langgraph import App

from api_agent import APIAgent
from runtime import AgentRuntime


def register(
    app: App,
    runtime: AgentRuntime,
    agent: APIAgent | None = None,
    factory: Callable[[AgentRuntime], APIAgent] | None = None,
) -> None:
    """Expose a direct AgentEnvelope endpoint for remote agent invocations."""

    def service() -> APIAgent:
        if agent is not None:
            return agent
        if factory is not None:
            return factory(runtime)
        return APIAgent(runtime.repository, runtime.artifacts)

    @app.tool(is_local=False)
    async def invoke_api_agent(envelope: dict[str, Any]) -> dict[str, Any]:
        """Generate or repair API artifacts from an AgentEnvelope request."""
        return (await service().execute_payload(envelope)).to_dict()
