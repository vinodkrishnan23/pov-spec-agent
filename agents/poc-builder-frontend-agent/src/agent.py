"""Persistent Frontend Agent workflow."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from artifacts import ArtifactStore
from contracts import AgentEnvelope, AgentRequest, AgentResponse, Artifact
from generation import generate_frontend
from ids import new_id, next_version
from tools.metadata import MetadataRepository


class FrontendAgent:
    """Generate or repair versioned React/Vite frontend artifacts."""

    name = "frontend_agent"

    def __init__(
        self, repository: MetadataRepository, artifacts: ArtifactStore
    ) -> None:
        self.repository = repository
        self.artifacts = artifacts

    async def execute_payload(self, value: dict[str, Any]) -> AgentEnvelope:
        """Normalize a POC-scoped HTTP request into an AgentEnvelope."""
        request_value = dict(value.get("request", value))
        poc_id = str(request_value.get("poc_id", ""))
        if not poc_id:
            raise ValueError("request.poc_id is required")
        request = AgentRequest(
            poc_id=poc_id,
            run_id=str(request_value.get("run_id") or new_id("run")),
            task_id=str(request_value.get("task_id") or new_id("task")),
            trace_id=str(request_value.get("trace_id") or new_id("task")),
            caller=str(request_value.get("caller") or "remote_agent"),
            agent=str(request_value.get("agent") or self.name),
            tool=str(request_value.get("tool") or "invoke_frontend_agent"),
            mode=str(request_value.get("mode") or "generate"),
            params=dict(request_value.get("params") or {}),
            deadline_at=request_value.get("deadline_at"),
            max_tokens=int(
                dict(request_value.get("budget") or {}).get(
                    "max_tokens", request_value.get("max_tokens", 200_000)
                )
            ),
        )
        if request.agent != self.name:
            raise ValueError(f"Envelope targets {request.agent}, expected {self.name}")
        return await self.execute(AgentEnvelope(request))

    async def execute(self, envelope: AgentEnvelope) -> AgentEnvelope:
        request = await self._hydrate_request(envelope.request)
        envelope = AgentEnvelope(request)
        self.repository.create_task(request.to_dict())
        try:
            if request.mode not in {"generate", "repair"}:
                raise ValueError(f"Unsupported Frontend Agent mode: {request.mode}")
            params = request.params
            code_version = str(params["code_version"])
            stories = params.get("user_stories", [])
            if not isinstance(stories, list):
                raise TypeError("user_stories must be a list")
            files = generate_frontend(
                str(params.get("title", "Generated POC")),
                stories,
                "openapi: 3.1.0\npaths: {}\n",
                repair_notes=params.get("failure")
                if request.mode == "repair"
                else None,
                source_inputs=params.get("spec_inputs"),
            )
            artifacts: list[Artifact] = []
            for relative, body in files.items():
                key = relative
                await self.artifacts.put(
                    request.poc_id,
                    request.run_id,
                    key,
                    body,
                    "application/json" if relative.endswith(".json") else "text/plain",
                    self.name,
                )
                artifacts.append(Artifact("code", key, code_version))
            response = AgentResponse.succeeded(
                request,
                {"frontend_prefix": "frontend/", "files": list(files)},
                tuple(artifacts),
            )
            git = self._record_git_location(request.poc_id, code_version, "frontend")
            if git:
                response = replace(response, result={**response.result, "git": git})
            self.repository.finish_task(request.task_id, "succeeded", artifacts[0].key)
            return envelope.with_response(response)
        except Exception as error:  # noqa: BLE001 - agent boundary returns a structured error
            response = AgentResponse.failed(
                request, "FRONTEND_GENERATION_FAILED", str(error)
            )
            self.repository.finish_task(request.task_id, "failed", error=response.error)
            return envelope.with_response(response)

    async def _hydrate_request(self, request: AgentRequest) -> AgentRequest:
        poc = self.repository.get_poc(request.poc_id)
        params = dict(request.params)
        spec_inputs = await self._load_spec_inputs(
            poc, ("frontend_contract", "requirements")
        )
        requirements = dict(spec_inputs["requirements"])
        frontend_contract = dict(spec_inputs["frontend_contract"])
        params.setdefault(
            "title",
            str(frontend_contract.get("app_name", poc.get("title", "Untitled POC"))),
        )
        params.setdefault(
            "user_stories",
            requirements.get("user_stories")
            or [
                {"id": identifier, "title": detail.get("title", identifier)}
                for identifier, detail in dict(requirements.get("use_cases", {})).items()
                if isinstance(detail, dict)
            ],
        )
        params.setdefault("spec_inputs", spec_inputs)
        if "code_version" not in params:
            current_version = poc.get("current_versions", {}).get("code")
            params["code_version"] = (
                current_version
                if request.mode == "repair" and current_version
                else next_version(current_version)
            )
        return replace(request, params=params)

    async def _load_spec_inputs(
        self, poc: dict[str, Any], names: tuple[str, ...]
    ) -> dict[str, Any]:
        assets = dict(poc.get("spec_artifacts", {}))
        inputs: dict[str, Any] = {}
        for name in names:
            asset = assets.get(name)
            url = asset.get("url") if isinstance(asset, dict) else None
            if not isinstance(url, str) or not url:
                raise ValueError(f"POC spec_artifacts.{name}.url is required")
            inputs[name] = json.loads(await self.artifacts.get_url_text(url))
        return inputs

    def _record_git_location(
        self, poc_id: str, code_version: str, folder: str
    ) -> dict[str, str] | None:
        repository_url = getattr(self.artifacts, "repository_url", "")
        branch_for = getattr(self.artifacts, "branch_for", None)
        if not repository_url or not callable(branch_for):
            return None
        branch = str(branch_for(poc_id))
        git = {
            "repository_url": str(repository_url),
            "branch": branch,
            "folder": folder,
            "folder_url": f"{repository_url}/tree/{quote(branch, safe='')}/{folder}",
        }
        poc = self.repository.get_poc(poc_id)
        artifacts = dict(poc.get("artifacts", {}))
        code = dict(artifacts.get("code", {}))
        code.update({"version": code_version, "git": git})
        artifacts["code"] = code
        self.repository.update_poc(poc_id, artifacts=artifacts)
        return git

    async def _input_text(
        self, poc_id: str, params: dict[str, Any], input_name: str
    ) -> str:
        inline = params.get(input_name)
        if isinstance(inline, str) and inline:
            return inline
        key = params.get(f"{input_name}_key")
        if not isinstance(key, str) or not key:
            raise ValueError(f"{input_name} or {input_name}_key is required")
        return await self.artifacts.get_text(poc_id, key)
