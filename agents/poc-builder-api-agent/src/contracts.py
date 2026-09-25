"""Shared contracts used across POC Builder agents and tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, cast

from ids import new_id

AgentStatus = Literal["succeeded", "failed", "needs_clarification", "started"]
RunStatus = Literal[
    "queued", "running", "waiting_user", "succeeded", "failed", "cancelled"
]
Component = Literal["seed", "backend", "frontend", "unknown"]


@dataclass(frozen=True)
class Artifact:
    kind: str
    key: str
    version: str


@dataclass(frozen=True)
class AgentRequest:
    poc_id: str
    run_id: str
    task_id: str
    trace_id: str
    caller: str
    agent: str
    tool: str
    mode: str
    params: dict[str, Any] = field(default_factory=dict)
    deadline_at: str | None = None
    max_tokens: int = 200_000

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AgentRequest:
        budget = value.get("budget", {})
        return cls(
            poc_id=str(value["poc_id"]),
            run_id=str(value.get("run_id") or new_id("run")),
            task_id=str(value.get("task_id") or new_id("task")),
            trace_id=str(value.get("trace_id") or new_id("task")),
            caller=str(value.get("caller") or "remote_agent"),
            agent=str(value.get("agent") or "unknown"),
            tool=str(value.get("tool") or "invoke_agent"),
            mode=str(value.get("mode", "generate")),
            params=cast(dict[str, Any], value.get("params", {})),
            deadline_at=value.get("deadline_at"),
            max_tokens=int(budget.get("max_tokens", value.get("max_tokens", 200_000))),
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        max_tokens = value.pop("max_tokens")
        value["budget"] = {"max_tokens": max_tokens}
        return value


@dataclass(frozen=True)
class AgentResponse:
    task_id: str
    status: AgentStatus
    result: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[Artifact, ...] = ()
    error: dict[str, Any] | None = None
    usage: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def succeeded(
        cls,
        request: AgentRequest,
        result: dict[str, Any],
        artifacts: tuple[Artifact, ...] = (),
        status: AgentStatus = "succeeded",
    ) -> AgentResponse:
        return cls(
            task_id=request.task_id, status=status, result=result, artifacts=artifacts
        )

    @classmethod
    def failed(
        cls,
        request: AgentRequest,
        code: str,
        message: str,
        retryable: bool = False,
        detail: dict[str, Any] | None = None,
    ) -> AgentResponse:
        return cls(
            task_id=request.task_id,
            status="failed",
            error={
                "code": code,
                "message": message,
                "retryable": retryable,
                "detail": detail or {},
            },
        )


@dataclass(frozen=True)
class FailureReport:
    poc_id: str
    deploy_run_id: str
    code_version: str
    component: Component
    stage_step: str
    failure_class: str
    attempt: int
    max_attempts: int = 3
    exit_code: int | None = None
    stdout_key: str = ""
    stderr_excerpt: str = ""
    test_result_ids: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()

    @property
    def can_retry(self) -> bool:
        return self.component != "unknown" and self.attempt < self.max_attempts


@dataclass(frozen=True)
class AgentEnvelope:
    request: AgentRequest
    response: AgentResponse | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AgentEnvelope:
        request_value = cast(dict[str, Any], value.get("request", value))
        return cls(request=AgentRequest.from_dict(request_value))

    def with_response(self, response: AgentResponse) -> AgentEnvelope:
        return AgentEnvelope(request=self.request, response=response)

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"request": self.request.to_dict()}
        if self.response is not None:
            value["response"] = self.response.to_dict()
        return value
