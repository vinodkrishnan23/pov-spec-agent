"""Magenta tools exposed by the Coding Orchestrator."""

from __future__ import annotations

import json
from collections.abc import Callable

from agent_engine_sdk_langgraph import App

from contracts import AgentEnvelope, FailureReport
from orchestrator import CodingOrchestrator
from runtime import AgentRuntime
from tooling import build_request, parse_envelope


def register(
    app: App,
    runtime: AgentRuntime,
    agent: CodingOrchestrator | None = None,
    factory: Callable[[AgentRuntime], CodingOrchestrator] | None = None,
) -> None:
    def service() -> CodingOrchestrator:
        if agent is not None:
            return agent
        if factory is None:
            raise RuntimeError("Coding Orchestrator service factory is not configured")
        return factory(runtime)

    def current_user_id() -> str:
        return app.get_current_user_id() or "anonymous"

    @app.tool(is_local=False)
    async def invoke_coding_orchestrator(envelope_json: str) -> str:
        """Execute a queued Coding Orchestrator run using AgentEnvelope JSON."""
        result = await service().execute(
            parse_envelope(envelope_json, "coding_orchestrator")
        )
        return json.dumps(result.to_dict())

    @app.tool(is_local=False)
    def start_code_run(poc_id: str, spec_version: str) -> str:
        """Queue Stage 2 when required specification artifact links are present."""
        return json.dumps(service().start(poc_id, spec_version, current_user_id()))

    @app.tool(is_local=False)
    async def process_code_run(run_id: str) -> str:
        """Execute a queued code run and persist generated artifacts."""
        run = runtime.repository.get_run(run_id)
        request = build_request(
            run["poc_id"],
            run_id,
            "chat_agent",
            "coding_orchestrator",
            "start_code_run",
            "generate",
            {},
        )
        result = await service().execute(AgentEnvelope(request))
        return json.dumps(result.to_dict())

    @app.tool(is_local=False)
    async def repair_component(failure_json: str) -> str:
        """Repair one failed seed, backend, or frontend component from a FailureReport."""
        result = await service().repair_component(
            FailureReport(**json.loads(failure_json))
        )
        return json.dumps(result)
