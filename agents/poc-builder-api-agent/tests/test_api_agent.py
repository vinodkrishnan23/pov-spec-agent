import json

import pytest

from api_agent import APIAgent
from artifacts import InMemoryArtifactStore
from generation import generate_contract
from main import app, build_agent
from tools.metadata import InMemoryMetadataRepository


def test_deployment_exposes_graph_without_init_logic() -> None:
    assert app is not None
    assert callable(build_agent)


def test_api_agent_generates_openapi_contract() -> None:
    contract = generate_contract("Demo", [{"id": "us-01", "title": "List items"}], {})

    assert contract.startswith("openapi: 3.1.0")


class GitArtifactStore(InMemoryArtifactStore):
    repository_url = "https://github.com/example/poc-artifacts"

    @staticmethod
    def branch_for(poc_id: str) -> str:
        return f"poc/{poc_id}"


@pytest.mark.asyncio
async def test_poc_only_envelope_loads_poc_inputs_and_records_git_folder() -> None:
    repository = InMemoryMetadataRepository()
    artifacts = GitArtifactStore()
    poc_id = "poc_0123456789abcdef0123456789"
    repository.create_poc(poc_id, "Inventory", "user-1")
    assets = {
        name: {"url": f"https://github.com/example/spec/{name}.json"}
        for name in ("requirements", "api_contract", "query_patterns", "data_model")
    }
    repository.update_poc(poc_id, spec_artifacts=assets)
    artifacts.url_objects.update(
        {
            assets["requirements"]["url"]: json.dumps(
                {"user_stories": [{"id": "us-01", "title": "List products"}]}
            ).encode(),
            assets["api_contract"]["url"]: b'{"service": "inventory"}',
            assets["query_patterns"]["url"]: b'{"QP-1": {}}',
            assets["data_model"]["url"]: b'{"collections": [{"name": "products"}]}',
        }
    )

    result = await APIAgent(repository, artifacts).execute_payload(
        {"request": {"poc_id": poc_id}}
    )

    assert result.response is not None
    assert result.response.status == "succeeded"
    assert result.request.agent == "api_agent"
    assert result.request.run_id.startswith("run_")
    assert result.response.result["backend_prefix"] == "backend/"
    git = repository.get_poc(poc_id)["artifacts"]["code"]["git"]
    assert git == result.response.result["git"]
    assert git["branch"] == f"poc/{poc_id}"
    assert git["folder"] == "backend"
    assert "backend/generation-inputs.json" in artifacts.objects
