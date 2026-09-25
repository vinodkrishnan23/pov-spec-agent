import inspect
import json

import httpx
import pytest

from agent_client import HTTPAgentClient
from artifacts import InMemoryArtifactStore
from config import Settings
from contracts import AgentEnvelope, AgentRequest, AgentResponse
from generation import repair_component
from ids import new_id
from main import app, build_agent, build_service
from orchestrator import CodingOrchestrator
from runtime import AgentRuntime
from tools.metadata import InMemoryMetadataRepository


def test_deployment_exposes_graph_without_init_logic() -> None:
    assert app is not None and callable(build_agent)
    assert not inspect.getmembers(__import__("tools"), inspect.isfunction)


def test_repairs_are_bounded() -> None:
    assert repair_component("frontend", 2)["status"] == "started"
    assert repair_component("frontend", 3)["error"]["code"] == "REPAIR_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_agent_client_uses_oauth_and_agent_engine_invoke_stream() -> None:
    request = AgentRequest(
        poc_id=new_id("poc"),
        run_id=new_id("run"),
        task_id=new_id("task"),
        trace_id=new_id("task"),
        caller="coding_orchestrator",
        agent="api_agent",
        tool="generate_api",
        mode="generate",
        params={"code_version": "v001"},
        deadline_at="2026-09-25T12:00:00Z",
    )

    async def handler(incoming: httpx.Request) -> httpx.Response:
        if incoming.url.path == "/api/v1/oauth/token":
            assert incoming.headers["authorization"].startswith("Basic ")
            assert incoming.content == b"grant_type=client_credentials"
            return httpx.Response(200, json={"access_token": "test-token"})
        assert incoming.headers["authorization"] == "Bearer test-token"
        payload = json.loads(incoming.content)
        assert payload == {"message": json.dumps(AgentEnvelope(request).to_dict())}
        response = AgentResponse.succeeded(request, {"contract_key": "pocs/demo.yaml"})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"data: {json.dumps(AgentEnvelope(request, response).to_dict())}\n\ndata: [DONE]\n\n",
        )

    client = HTTPAgentClient(
        "https://agentengine.mongodb.com/api/v1/projects/project/workspaces/workspace/invokeStream",
        "api-client-id",
        "api-client-secret",
        transport=httpx.MockTransport(handler),
    )
    result = await client.execute(AgentEnvelope(request))

    assert result.request.run_id == request.run_id
    assert result.request.trace_id == request.trace_id
    assert result.response is not None
    assert result.response.task_id == request.task_id


def test_service_uses_agent_urls_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POC_API_AGENT_URL", "https://agents.example.test/api")
    monkeypatch.setenv("POC_API_AGENT_CLIENT_ID", "api-client-id")
    monkeypatch.setenv("POC_API_AGENT_CLIENT_SECRET", "api-client-secret")
    monkeypatch.setenv("POC_DATA_SEEDING_AGENT_URL", "https://agents.example.test/seed")
    monkeypatch.setenv("POC_DATA_SEEDING_AGENT_CLIENT_ID", "seed-client-id")
    monkeypatch.setenv("POC_DATA_SEEDING_AGENT_CLIENT_SECRET", "seed-client-secret")
    monkeypatch.setenv("POC_FRONTEND_AGENT_URL", "https://agents.example.test/frontend")
    monkeypatch.setenv("POC_FRONTEND_AGENT_CLIENT_ID", "frontend-client-id")
    monkeypatch.setenv("POC_FRONTEND_AGENT_CLIENT_SECRET", "frontend-client-secret")
    runtime = AgentRuntime(
        repository=InMemoryMetadataRepository(),
        artifacts=InMemoryArtifactStore(),
        settings=Settings(metadata_backend="memory", artifact_backend="memory"),
    )

    service = build_service(runtime)

    assert isinstance(service.api_agent, HTTPAgentClient)
    assert service.api_agent.endpoint.endswith("/api")
    assert service.api_agent.client_id == "api-client-id"
    assert isinstance(service.data_seeding_agent, HTTPAgentClient)
    assert service.data_seeding_agent.endpoint.endswith("/seed")
    assert service.data_seeding_agent.client_id == "seed-client-id"
    assert isinstance(service.frontend_agent, HTTPAgentClient)
    assert service.frontend_agent.endpoint.endswith("/frontend")
    assert service.frontend_agent.client_id == "frontend-client-id"


class UnusedAgent:
    async def execute(self, envelope: AgentEnvelope) -> AgentEnvelope:
        raise AssertionError(f"Child agent must not run: {envelope.request.agent}")


class GeneratedComponentAgent:
    def __init__(self, artifacts: InMemoryArtifactStore, component: str) -> None:
        self.artifacts = artifacts
        self.component = component

    async def execute(self, envelope: AgentEnvelope) -> AgentEnvelope:
        request = envelope.request
        code_version = str(request.params["code_version"])
        base = f"pocs/{request.poc_id}/code/{code_version}"
        if request.mode == "contract":
            key = f"{base}/api_contract.yaml"
            content = "openapi: 3.1.0\npaths: {}\n"
            result = {"contract_key": key}
            kind = "contract"
        else:
            key = f"{base}/{self.component}/generated.txt"
            content = f"{self.component} generated"
            result = {}
            kind = "code"
        await self.artifacts.put(
            request.poc_id, request.run_id, key, content, "text/plain", self.component
        )
        return envelope.with_response(
            AgentResponse.succeeded(request, result, (Artifact(kind, key, code_version),))
        )


@pytest.mark.asyncio
async def test_legacy_spec_artifact_links_support_latest_code_run() -> None:
    repository = InMemoryMetadataRepository()
    artifacts = InMemoryArtifactStore()
    poc_id = new_id("poc")
    repository.create_poc(poc_id, "Legacy POV", "user-1")
    urls = {
        name: f"https://github.com/example/repo/blob/main/{name}.json"
        for name in (
            "data_model",
            "query_patterns",
            "api_contract",
            "frontend_contract",
            "requirements",
        )
    }
    artifacts.url_objects[urls["data_model"]] = b'{"collections": []}'
    artifacts.url_objects[urls["query_patterns"]] = b'{"patterns": []}'
    artifacts.url_objects[urls["requirements"]] = b'{"user_stories": []}'
    repository.update_poc(
        poc_id,
        spec_artifacts={name: {"url": url} for name, url in urls.items()},
    )
    api = GeneratedComponentAgent(artifacts, "backend")
    agent = CodingOrchestrator(
        repository,
        artifacts,
        api,
        data_seeding_agent=GeneratedComponentAgent(artifacts, "seed"),
        frontend_agent=GeneratedComponentAgent(artifacts, "frontend"),
    )
    started = agent.start(poc_id, "latest", "user-1")
    request = AgentRequest(
        poc_id=poc_id,
        run_id=str(started["run_id"]),
        task_id=new_id("task"),
        trace_id=new_id("task"),
        caller="chat_agent",
        agent="coding_orchestrator",
        tool="process_code_run",
        mode="generate",
    )

    result = await agent.execute(AgentEnvelope(request))

    assert result.response is not None
    assert result.response.status == "succeeded"
    assert result.response.result["spec_url"] == urls["requirements"]
    assert repository.get_poc(poc_id)["status"] == "code_ready"


@pytest.mark.asyncio
async def test_missing_spec_artifact_links_fail_before_task_creation() -> None:
    repository = InMemoryMetadataRepository()
    artifacts = InMemoryArtifactStore()
    poc_id = new_id("poc")
    repository.create_poc(poc_id, "Northwind Support POV", "user-1")
    repository.update_poc(
        poc_id,
        spec_artifacts={
            "data_model": {"url": "https://github.com/example/repo/blob/main/data.json"}
        },
    )
    agent = CodingOrchestrator(
        repository,
        artifacts,
        UnusedAgent(),
        data_seeding_agent=UnusedAgent(),
        frontend_agent=UnusedAgent(),
    )
    request = AgentRequest(
        poc_id=poc_id,
        run_id=new_id("run"),
        task_id=new_id("task"),
        trace_id=new_id("task"),
        caller="chat_agent",
        agent="coding_orchestrator",
        tool="invoke_coding_orchestrator",
        mode="generate",
    )

    result = await agent.execute(AgentEnvelope(request))

    assert result.response is not None
    assert result.response.error is not None
    assert result.response.error["code"] == "REQUIRED_SPEC_ARTIFACT_LINKS_MISSING"
    assert result.response.error["detail"]["missing"] == [
        "query_patterns",
        "api_contract",
        "frontend_contract",
        "requirements",
    ]
    assert repository.tasks == {}
    assert repository.runs == {}
