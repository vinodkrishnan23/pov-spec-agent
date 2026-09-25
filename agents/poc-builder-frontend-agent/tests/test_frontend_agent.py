import json

import pytest

from agent import FrontendAgent
from artifacts import InMemoryArtifactStore
from generation import generate_frontend
from main import app, build_agent
from tools.metadata import InMemoryMetadataRepository


def test_deployment_exposes_graph_without_init_logic() -> None:
    assert app is not None and callable(build_agent)


def test_frontend_generation_preserves_testids() -> None:
    files = generate_frontend(
        "Demo",
        [{"id": "us-01", "title": "List", "testids": ["item-list"]}],
        "openapi: 3.1.0\npaths: {}\n",
    )

    assert 'data-testid="item-list"' in files["frontend/src/App.tsx"]


class GitArtifactStore(InMemoryArtifactStore):
    repository_url = "https://github.com/example/poc-artifacts"

    @staticmethod
    def branch_for(poc_id: str) -> str:
        return f"poc/{poc_id}"


@pytest.mark.asyncio
async def test_poc_only_envelope_records_git_folder() -> None:
    repository = InMemoryMetadataRepository()
    artifacts = GitArtifactStore()
    poc_id = "poc_0123456789abcdef0123456789"
    repository.create_poc(poc_id, "Inventory", "user-1")
    assets = {
        name: {"url": f"https://github.com/example/spec/{name}.json"}
        for name in ("frontend_contract", "requirements")
    }
    repository.update_poc(poc_id, spec_artifacts=assets)
    artifacts.url_objects.update(
        {
            assets["frontend_contract"]["url"]: json.dumps(
                {"app_name": "Inventory", "routes": []}
            ).encode(),
            assets["requirements"]["url"]: json.dumps(
                {"use_cases": {"UC-1": {"title": "List products"}}}
            ).encode(),
        }
    )

    result = await FrontendAgent(repository, artifacts).execute_payload(
        {"request": {"poc_id": poc_id}}
    )

    assert result.response is not None
    assert result.response.status == "succeeded"
    git = repository.get_poc(poc_id)["artifacts"]["code"]["git"]
    assert git == result.response.result["git"]
    assert git["branch"] == f"poc/{poc_id}"
    assert git["folder"] == "frontend"


@pytest.mark.asyncio
async def test_poc_only_envelope_loads_poc_inputs() -> None:
    repository = InMemoryMetadataRepository()
    artifacts = InMemoryArtifactStore()
    poc_id = "poc_0123456789abcdef0123456789"
    repository.create_poc(poc_id, "Inventory", "user-1")
    assets = {
        name: {"url": f"https://github.com/example/spec/{name}.json"}
        for name in ("frontend_contract", "requirements")
    }
    repository.update_poc(poc_id, spec_artifacts=assets)
    artifacts.url_objects.update(
        {
            assets["frontend_contract"]["url"]: json.dumps(
                {"app_name": "Inventory", "routes": []}
            ).encode(),
            assets["requirements"]["url"]: json.dumps(
                {"use_cases": {"UC-1": {"title": "List products"}}}
            ).encode(),
        }
    )

    result = await FrontendAgent(repository, artifacts).execute_payload(
        {"request": {"poc_id": poc_id}}
    )

    assert result.response is not None
    assert result.response.status == "succeeded"
    assert result.request.agent == "frontend_agent"
    assert result.request.run_id.startswith("run_")
    assert result.response.result["frontend_prefix"] == "frontend/"
    assert "frontend/generation-inputs.json" in artifacts.objects
