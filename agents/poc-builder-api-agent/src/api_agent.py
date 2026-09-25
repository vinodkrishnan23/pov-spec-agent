"""Persistent API Agent workflow."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from artifacts import ArtifactStore
from contracts import AgentEnvelope, AgentRequest, AgentResponse, Artifact
from generation import (
    generate_backend,
    generate_contract,
    story_dicts,
)
from ids import new_id, next_version
from tools.metadata import MetadataRepository


class APIAgent:
    """Generate contract or backend artifacts using the shared envelope."""

    name = "api_agent"

    def __init__(
        self, repository: MetadataRepository, artifacts: ArtifactStore
    ) -> None:
        self.repository = repository
        self.artifacts = artifacts

    async def execute_payload(self, value: dict[str, Any]) -> AgentEnvelope:
        """Materialize a POC-scoped HTTP request into the canonical envelope."""
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
            tool=str(request_value.get("tool") or "invoke_api_agent"),
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
            code_version = str(request.params["code_version"])
            base = "backend"
            if request.mode in {"contract", "generate"}:
                contract = generate_contract(
                    str(request.params["title"]),
                    story_dicts(request.params.get("user_stories", [])),
                    request.params.get("schema_design", {}),
                )
                key = f"{base}/api_contract.yaml"
                await self.artifacts.put(
                    request.poc_id,
                    request.run_id,
                    key,
                    contract,
                    "application/yaml",
                    self.name,
                )
                contract_artifact = Artifact("contract", key, code_version)
                if request.mode == "contract":
                    response = AgentResponse.succeeded(
                        request,
                        {"contract_key": key},
                        (contract_artifact,),
                    )
                else:
                    response = await self._write_backend(
                        request, code_version, base, contract, key, (contract_artifact,)
                    )
            elif request.mode in {"code", "repair"}:
                contract_key = "backend/api_contract.yaml"
                contract = generate_contract(
                    str(request.params["title"]),
                    story_dicts(request.params.get("user_stories", [])),
                    request.params.get("schema_design", {}),
                )
                response = await self._write_backend(
                    request, code_version, base, contract, contract_key
                )
            else:
                response = AgentResponse.failed(
                    request,
                    "UNSUPPORTED_MODE",
                    f"Unsupported API Agent mode: {request.mode}",
                )
            if response.status == "succeeded" and request.mode in {
                "code",
                "repair",
                "generate",
            }:
                git = self._record_git_location(request.poc_id, code_version, base)
                if git:
                    response = replace(response, result={**response.result, "git": git})
            output_ref = response.artifacts[0].key if response.artifacts else None
            self.repository.finish_task(
                request.task_id,
                response.status,
                output_ref=output_ref,
                error=response.error,
            )
            return envelope.with_response(response)
        except Exception as error:  # noqa: BLE001 - tool boundary returns structured failures
            response = AgentResponse.failed(
                request, "API_GENERATION_FAILED", str(error)
            )
            self.repository.finish_task(request.task_id, "failed", error=response.error)
            return envelope.with_response(response)

    async def _write_backend(
        self,
        request: AgentRequest,
        code_version: str,
        base: str,
        contract: str,
        contract_key: str,
        prefix: tuple[Artifact, ...] = (),
    ) -> AgentResponse:
        files = generate_backend(
            contract,
            request.params.get("schema_design", {}),
            repair_notes=request.params.get("failure")
            if request.mode == "repair"
            else None,
            source_inputs=request.params.get("spec_inputs"),
        )
        produced = list(prefix)
        for relative, body in files.items():
            key = relative
            content_type = (
                "application/json" if relative.endswith(".json") else "text/plain"
            )
            await self.artifacts.put(
                request.poc_id,
                request.run_id,
                key,
                body,
                content_type,
                self.name,
            )
            produced.append(Artifact("code", key, code_version))
        return AgentResponse.succeeded(
            request,
            {
                "contract_key": contract_key,
                "backend_prefix": "backend/",
                "files": list(files),
            },
            tuple(produced),
        )

    async def _hydrate_request(self, request: AgentRequest) -> AgentRequest:
        poc = self.repository.get_poc(request.poc_id)
        params = dict(request.params)
        specification = dict(
            poc.get("poc_specification", poc.get("specification", {})) or {}
        )
        spec_inputs = await self._load_spec_inputs(
            poc, ("requirements", "api_contract", "query_patterns", "data_model")
        )
        requirements = dict(spec_inputs["requirements"])
        params.setdefault("title", str(poc.get("title", "Untitled POC")))
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
        if "schema_design" not in params:
            schema = spec_inputs["data_model"] or specification.get(
                "schema_design", specification.get("schema", {})
            )
            params["schema_design"] = schema or {}
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
        code.update(
            {
                "version": code_version,
                "contract": f"{folder}/api_contract.yaml",
                "git": git,
            }
        )
        artifacts["code"] = code
        self.repository.update_poc(poc_id, artifacts=artifacts)
        return git
