"""Transport clients for invoking agents deployed in separate Magenta instances."""

from __future__ import annotations

from typing import Any

import httpx

from contracts import AgentEnvelope, AgentRequest, AgentResponse, Artifact


class HTTPAgentClient:
    """Invoke an AgentEnvelope-compatible remote endpoint over authenticated HTTP."""

    def __init__(
        self,
        endpoint: str,
        token: str = "",
        timeout_seconds: int = 300,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not endpoint:
            raise ValueError("A remote agent endpoint is required")
        self.endpoint = endpoint
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def execute(self, envelope: AgentEnvelope) -> AgentEnvelope:
        value = await self.call(envelope.to_dict())
        request_value = value.get("request", envelope.request.to_dict())
        response_value = value.get("response")
        request = AgentRequest.from_dict(request_value)
        if not isinstance(response_value, dict):
            return AgentEnvelope(request)
        artifacts = tuple(
            Artifact(
                kind=str(item["kind"]),
                key=str(item["key"]),
                version=str(item["version"]),
            )
            for item in response_value.get("artifacts", [])
        )
        response = AgentResponse(
            task_id=str(response_value["task_id"]),
            status=response_value["status"],
            result=dict(response_value.get("result", {})),
            artifacts=artifacts,
            error=response_value.get("error"),
            usage=dict(response_value.get("usage", {})),
        )
        return AgentEnvelope(request=request, response=response)

    async def call(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, transport=self.transport
        ) as client:
            response = await client.post(self.endpoint, json=payload, headers=headers)
            response.raise_for_status()
            value = response.json()
        if isinstance(value, str):
            import json

            value = json.loads(value)
        if isinstance(value, dict) and set(value) == {"result"}:
            value = value["result"]
            if isinstance(value, str):
                import json

                value = json.loads(value)
        if not isinstance(value, dict):
            raise TypeError("Remote agent response must be a JSON object")
        return value
