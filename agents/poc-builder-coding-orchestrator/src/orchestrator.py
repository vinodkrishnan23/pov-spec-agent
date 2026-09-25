"""Persistent contract-first Coding Orchestrator workflow."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

from artifacts import ArtifactStore
from contracts import (
    AgentEnvelope,
    AgentRequest,
    AgentResponse,
    Artifact,
    FailureReport,
)
from generation import tar_bundle
from ids import new_id, next_version
from tools.guardrails import scan_text
from tools.metadata import MetadataRepository


class AgentExecutor(Protocol):
    async def execute(self, envelope: AgentEnvelope) -> AgentEnvelope: ...


class CodingOrchestrator:
    """Coordinate Stage 2 through persistent, contract-first child-agent calls."""

    name = "coding_orchestrator"
    required_spec_artifacts = (
        "data_model",
        "query_patterns",
        "api_contract",
        "frontend_contract",
        "requirements",
    )

    def __init__(
        self,
        repository: MetadataRepository,
        artifacts: ArtifactStore,
        api_agent: AgentExecutor,
        presigned_url_ttl_seconds: int = 900,
        data_seeding_agent: AgentExecutor | None = None,
        frontend_agent: AgentExecutor | None = None,
    ) -> None:
        if data_seeding_agent is None or frontend_agent is None:
            raise ValueError("Remote Data Seeding and Frontend clients are required")
        self.repository = repository
        self.artifacts = artifacts
        self.api_agent = api_agent
        self.presigned_url_ttl_seconds = presigned_url_ttl_seconds
        self.data_seeding_agent = data_seeding_agent
        self.frontend_agent = frontend_agent

    def start(
        self, poc_id: str, spec_version: str, requested_by: str
    ) -> dict[str, Any]:
        try:
            poc = self.repository.get_poc(poc_id)
        except KeyError:
            return {
                "status": "failed",
                "error": {"code": "POC_NOT_FOUND", "poc_id": poc_id},
            }
        missing = self._missing_spec_artifact_links(poc)
        if missing:
            return self._missing_spec_artifacts_error(missing)
        # Code generation is gated by required spec artifact links, not approval state.
        run = self.repository.create_run(
            poc_id, "code", requested_by, {"spec_version": spec_version}
        )
        self.repository.update_poc(poc_id, status="coding")
        return {"status": "started", "run_id": run["run_id"]}

    async def repair_component(self, failure: FailureReport) -> dict[str, Any]:
        """Regenerate one failed component into a new immutable code version."""
        if not failure.can_retry:
            return {
                "status": "failed",
                "error": {
                    "code": "REPAIR_LIMIT_REACHED",
                    "component": failure.component,
                },
            }
        if failure.component not in {"seed", "backend", "frontend"}:
            return {
                "status": "failed",
                "error": {"code": "UNKNOWN_COMPONENT", "component": failure.component},
            }
        try:
            poc = self.repository.get_poc(failure.poc_id)
        except KeyError:
            return {
                "status": "failed",
                "error": {"code": "POC_NOT_FOUND", "poc_id": failure.poc_id},
            }
        missing = self._missing_spec_artifact_links(poc)
        if missing:
            return self._missing_spec_artifacts_error(missing)
        current_code = dict(poc.get("artifacts", {}).get("code", {}))
        source_version = str(current_code.get("version", failure.code_version))
        if source_version != failure.code_version:
            return {
                "status": "failed",
                "error": {
                    "code": "CODE_VERSION_MISMATCH",
                    "expected": failure.code_version,
                    "current": source_version,
                },
            }
        spec_inputs = await self._load_spec_inputs(
            poc, ("data_model", "query_patterns")
        )
        schema_design = dict(spec_inputs["data_model"])
        query_patterns = dict(spec_inputs["query_patterns"])
        repaired_version = next_version(source_version)
        source_base = f"pocs/{failure.poc_id}/code/{source_version}/"
        target_base = f"pocs/{failure.poc_id}/code/{repaired_version}/"
        source_objects = await self.artifacts.list(failure.poc_id, source_base)
        copied_keys: list[str] = []
        component_prefix = f"{failure.component}/"
        for item in source_objects:
            source_key = str(item["key"])
            relative = source_key.removeprefix(source_base)
            if relative.startswith(component_prefix) or relative in {
                "bundle.tar.gz",
                "poc.manifest.json",
            }:
                continue
            await self.artifacts.put(
                failure.poc_id,
                failure.deploy_run_id,
                f"{target_base}{relative}",
                await self.artifacts.get_text(failure.poc_id, source_key),
                "application/json" if relative.endswith(".json") else "text/plain",
                self.name,
            )
            copied_keys.append(f"{target_base}{relative}")

        request = AgentRequest(
            poc_id=failure.poc_id,
            run_id=failure.deploy_run_id,
            task_id=new_id("task"),
            trace_id=self.repository.get_run(failure.deploy_run_id)["trace_id"],
            caller=self.name,
            agent={
                "seed": "data_seeding_agent",
                "backend": "api_agent",
                "frontend": "frontend_agent",
            }[failure.component],
            tool=f"repair_{failure.component}",
            mode="repair",
            params={"code_version": repaired_version, "failure": failure.__dict__},
        )
        if failure.component == "seed":
            request = self._with_params(
                request,
                {"schema_design": schema_design, "query_patterns": query_patterns},
            )
            child = await self.data_seeding_agent.execute(AgentEnvelope(request))
        elif failure.component == "frontend":
            request = self._with_params(
                request,
                {
                    "title": poc["title"],
                    "user_stories": poc.get("draft_requirements", {}).get(
                        "user_stories", []
                    ),
                    "api_contract_key": current_code["contract"],
                },
            )
            child = await self.frontend_agent.execute(AgentEnvelope(request))
        else:
            request = self._with_params(
                request,
                {
                    "schema_design": schema_design,
                    "api_contract_key": current_code["contract"],
                },
            )
            child = await self.api_agent.execute(AgentEnvelope(request))
        if child.response is None or child.response.status != "succeeded":
            return {
                "status": "failed",
                "error": child.response.error
                if child.response
                else {"code": "REPAIR_FAILED"},
            }

        generated_keys = [artifact.key for artifact in child.response.artifacts]
        all_keys = [*copied_keys, *generated_keys]
        file_bodies = {
            key.removeprefix(target_base): await self.artifacts.get_text(
                failure.poc_id, key
            )
            for key in all_keys
        }
        violations = [
            violation
            for path, body in file_bodies.items()
            for violation in scan_text(body, path)
        ]
        if violations:
            return {
                "status": "failed",
                "error": {"code": "GUARDRAIL_VIOLATION", "detail": violations},
            }
        bundle_key = f"{target_base}bundle.tar.gz"
        manifest_key = f"{target_base}poc.manifest.json"
        manifest = {
            "poc_id": failure.poc_id,
            "code_version": repaired_version,
            "spec_version": self._spec_version(poc),
            "stack": poc["stack"],
            "components": ["seed", "backend", "frontend"],
            "contract_key": current_code["contract"],
            "bundle_key": bundle_key,
            "guardrail_scan": {"ok": True},
            "produced_by": {
                "run_id": failure.deploy_run_id,
                "repairs_of": source_version,
                "changed_components": [failure.component],
            },
        }
        file_bodies["poc.manifest.json"] = json.dumps(manifest, indent=2)
        await self.artifacts.put(
            failure.poc_id,
            failure.deploy_run_id,
            manifest_key,
            file_bodies["poc.manifest.json"],
            "application/json",
            self.name,
        )
        await self.artifacts.put(
            failure.poc_id,
            failure.deploy_run_id,
            bundle_key,
            tar_bundle(file_bodies),
            "application/gzip",
            self.name,
        )
        artifacts = dict(poc.get("artifacts", {}))
        artifacts["code"] = {
            "version": repaired_version,
            "manifest": manifest_key,
            "bundle": bundle_key,
            "contract": current_code["contract"],
        }
        self.repository.set_current_version(failure.poc_id, "code", repaired_version)
        self.repository.update_poc(
            failure.poc_id, artifacts=artifacts, status="code_ready"
        )
        return {
            "status": "succeeded",
            "code_version": repaired_version,
            "bundle_key": bundle_key,
            "manifest_key": manifest_key,
        }

    async def execute(self, envelope: AgentEnvelope) -> AgentEnvelope:
        request = envelope.request
        try:
            poc = self.repository.get_poc(request.poc_id)
        except KeyError:
            response = AgentResponse.failed(
                request,
                "POC_NOT_FOUND",
                f"No POC document exists for poc_id {request.poc_id}",
            )
            return envelope.with_response(response)
        missing = self._missing_spec_artifact_links(poc)
        if missing:
            error = self._missing_spec_artifacts_error(missing)["error"]
            response = AgentResponse.failed(
                request,
                str(error["code"]),
                str(error["message"]),
                detail={"missing": missing},
            )
            return envelope.with_response(response)
        self.repository.create_task(request.to_dict())
        if request.mode == "repair":
            failure_value = request.params.get("failure")
            if not isinstance(failure_value, dict):
                response = AgentResponse.failed(
                    request,
                    "INVALID_FAILURE_REPORT",
                    "params.failure must be an object",
                )
                self.repository.finish_task(
                    request.task_id, "failed", error=response.error
                )
                return envelope.with_response(response)
            result = await self.repair_component(FailureReport(**failure_value))
            if result.get("status") == "succeeded":
                response = AgentResponse.succeeded(request, result)
                self.repository.finish_task(
                    request.task_id, "succeeded", result.get("manifest_key")
                )
            else:
                error = result.get("error", {})
                response = AgentResponse.failed(
                    request,
                    str(error.get("code", "REPAIR_FAILED")),
                    str(error.get("message", "Component repair failed")),
                    detail=dict(error.get("detail", {})),
                )
                self.repository.finish_task(
                    request.task_id, "failed", error=response.error
                )
            return envelope.with_response(response)
        try:
            run = self.repository.get_run(request.run_id)
            spec_version = str(run["inputs"]["spec_version"])
            spec_inputs = await self._load_spec_inputs(
                poc, ("data_model", "query_patterns", "requirements")
            )
            schema_design = dict(spec_inputs["data_model"])
            query_patterns = dict(spec_inputs["query_patterns"])
            requirements = dict(spec_inputs["requirements"])
            stories = requirements.get(
                "user_stories", ["User completes the primary workflow"]
            )
            code_version = next_version(poc.get("current_versions", {}).get("code"))
            self.repository.update_run(request.run_id, status="running")

            self.repository.update_run_step(request.run_id, "load_inputs", "succeeded")
            contract_request = self._child_request(
                request,
                "api_agent",
                "generate_api",
                "contract",
                {
                    "code_version": code_version,
                    "title": poc["title"],
                    "user_stories": stories,
                    "schema_design": schema_design,
                },
            )
            self.repository.update_run_step(
                request.run_id, "generate_contract", "running"
            )
            contract_envelope = await self.api_agent.execute(
                AgentEnvelope(contract_request)
            )
            if (
                contract_envelope.response is None
                or contract_envelope.response.status != "succeeded"
            ):
                raise RuntimeError("API contract generation failed")
            contract_key = str(contract_envelope.response.result["contract_key"])
            self.repository.update_run_step(
                request.run_id, "generate_contract", "succeeded", contract_key
            )

            backend_request = self._child_request(
                request,
                "api_agent",
                "generate_api",
                "code",
                {
                    "code_version": code_version,
                    "api_contract_key": contract_key,
                    "schema_design": schema_design,
                },
            )
            self.repository.update_run_step(
                request.run_id, "generate_components", "running"
            )
            backend_envelope = await self.api_agent.execute(
                AgentEnvelope(backend_request)
            )
            if (
                backend_envelope.response is None
                or backend_envelope.response.status != "succeeded"
            ):
                raise RuntimeError("Backend generation failed")

            contract = await self.artifacts.get_text(request.poc_id, contract_key)
            seed_request = self._child_request(
                request,
                "data_seeding_agent",
                "generate_seed",
                "generate",
                {
                    "code_version": code_version,
                    "schema_design": schema_design,
                    "query_patterns": query_patterns,
                },
            )
            frontend_request = self._child_request(
                request,
                "frontend_agent",
                "generate_frontend",
                "generate",
                {
                    "code_version": code_version,
                    "title": poc["title"],
                    "user_stories": stories,
                    "api_contract": contract,
                },
            )
            seed_envelope, frontend_envelope = await asyncio.gather(
                self.data_seeding_agent.execute(AgentEnvelope(seed_request)),
                self.frontend_agent.execute(AgentEnvelope(frontend_request)),
            )
            child_responses = [seed_envelope.response, frontend_envelope.response]
            if any(
                response is None or response.status != "succeeded"
                for response in child_responses
            ):
                raise RuntimeError("Seed or frontend generation failed")
            generated_keys = [
                artifact.key
                for response in child_responses
                if response is not None
                for artifact in response.artifacts
            ]
            base = f"pocs/{request.poc_id}/code/{code_version}"
            self.repository.update_run_step(
                request.run_id, "generate_components", "succeeded", base
            )

            all_keys = [contract_key, *generated_keys] + [
                artifact.key for artifact in backend_envelope.response.artifacts
            ]
            file_bodies = {
                key.removeprefix(f"{base}/"): await self.artifacts.get_text(
                    request.poc_id, key
                )
                for key in all_keys
            }
            self.repository.update_run_step(request.run_id, "guardrail_scan", "running")
            violations = [
                violation
                for path, body in file_bodies.items()
                for violation in scan_text(body, path)
            ]
            if violations:
                raise ValueError(
                    json.dumps(
                        {"code": "GUARDRAIL_VIOLATION", "violations": violations}
                    )
                )
            self.repository.update_run_step(
                request.run_id, "guardrail_scan", "succeeded"
            )

            bundle_key = f"{base}/bundle.tar.gz"
            manifest_key = f"{base}/poc.manifest.json"
            manifest = {
                "poc_id": request.poc_id,
                "code_version": code_version,
                "spec_version": spec_version,
                "stack": poc["stack"],
                "components": ["seed", "backend", "frontend"],
                "contract_key": contract_key,
                "bundle_key": bundle_key,
                "guardrail_scan": {"ok": True},
                "produced_by": {
                    "run_id": request.run_id,
                    "repairs_of": request.params.get("repairs_of"),
                    "changed_components": request.params.get(
                        "changed_components", ["seed", "backend", "frontend"]
                    ),
                },
            }
            file_bodies["poc.manifest.json"] = json.dumps(manifest, indent=2)
            bundle = tar_bundle(file_bodies)
            await self.artifacts.put(
                request.poc_id,
                request.run_id,
                manifest_key,
                file_bodies["poc.manifest.json"],
                "application/json",
                self.name,
            )
            await self.artifacts.put(
                request.poc_id,
                request.run_id,
                bundle_key,
                bundle,
                "application/gzip",
                self.name,
            )
            spec_url = self._spec_asset_url(poc, "requirements")
            bundle_url = await self.artifacts.create_presigned_get_url(
                request.poc_id, bundle_key, self.presigned_url_ttl_seconds
            )
            poc_artifacts = dict(poc.get("artifacts", {}))
            poc_artifacts["code"] = {
                "version": code_version,
                "manifest": manifest_key,
                "bundle": bundle_key,
                "contract": contract_key,
            }
            self.repository.set_current_version(request.poc_id, "code", code_version)
            self.repository.update_poc(
                request.poc_id, status="code_ready", artifacts=poc_artifacts
            )
            self.repository.update_run_step(
                request.run_id, "finalize", "succeeded", bundle_key
            )
            self.repository.update_run(
                request.run_id,
                status="succeeded",
                outputs={
                    "code_version": code_version,
                    "bundle_key": bundle_key,
                    "spec_url": spec_url,
                    "bundle_url": bundle_url,
                },
            )
            response = AgentResponse.succeeded(
                request,
                {
                    "run_id": request.run_id,
                    "code_version": code_version,
                    "bundle_key": bundle_key,
                    "manifest_key": manifest_key,
                    "spec_url": spec_url,
                    "bundle_url": bundle_url,
                },
                (
                    Artifact("bundle", bundle_key, code_version),
                    Artifact("code", manifest_key, code_version),
                ),
            )
            self.repository.finish_task(request.task_id, "succeeded", bundle_key)
            return envelope.with_response(response)
        except Exception as error:  # noqa: BLE001 - run boundary persists structured failures
            response = AgentResponse.failed(
                request, "CODE_RUN_FAILED", str(error), retryable=False
            )
            self.repository.update_run(
                request.run_id, status="failed", error=response.error
            )
            self.repository.update_poc(request.poc_id, status="failed")
            self.repository.finish_task(request.task_id, "failed", error=response.error)
            return envelope.with_response(response)

    async def _load_spec_inputs(
        self, poc: dict[str, Any], names: tuple[str, ...]
    ) -> dict[str, Any]:
        return {
            name: json.loads(
                await self.artifacts.get_url_text(self._spec_asset_url(poc, name))
            )
            for name in names
        }

    @staticmethod
    def _spec_asset_url(poc: dict[str, Any], name: str) -> str:
        asset = dict(poc.get("spec_artifacts", {})).get(name)
        url = asset.get("url") if isinstance(asset, dict) else None
        if not isinstance(url, str) or not url:
            raise ValueError(f"POC spec_artifacts.{name}.url is required")
        return url

    @staticmethod
    def _spec_version(poc: dict[str, Any]) -> str:
        return str(poc.get("current_versions", {}).get("spec") or "latest")

    @classmethod
    def _missing_spec_artifact_links(cls, poc: dict[str, Any]) -> list[str]:
        artifacts = dict(poc.get("spec_artifacts", {}))
        return [
            name
            for name in cls.required_spec_artifacts
            if not isinstance(artifacts.get(name), dict)
            or not isinstance(artifacts[name].get("url"), str)
            or not artifacts[name]["url"].strip()
        ]

    @staticmethod
    def _missing_spec_artifacts_error(missing: list[str]) -> dict[str, Any]:
        return {
            "status": "failed",
            "error": {
                "code": "REQUIRED_SPEC_ARTIFACT_LINKS_MISSING",
                "message": "POC document is missing required spec_artifacts Git links",
                "missing": missing,
            },
        }

    @staticmethod
    def _with_params(request: AgentRequest, params: dict[str, Any]) -> AgentRequest:
        return AgentRequest(
            poc_id=request.poc_id,
            run_id=request.run_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            caller=request.caller,
            agent=request.agent,
            tool=request.tool,
            mode=request.mode,
            params={**request.params, **params},
            deadline_at=request.deadline_at,
            max_tokens=request.max_tokens,
        )

    @staticmethod
    def _child_request(
        parent: AgentRequest,
        agent: str,
        tool: str,
        mode: str,
        params: dict[str, Any],
    ) -> AgentRequest:
        return AgentRequest(
            poc_id=parent.poc_id,
            run_id=parent.run_id,
            task_id=new_id("task"),
            trace_id=parent.trace_id,
            caller="coding_orchestrator",
            agent=agent,
            tool=tool,
            mode=mode,
            params=params,
            deadline_at=parent.deadline_at,
            max_tokens=parent.max_tokens,
        )
