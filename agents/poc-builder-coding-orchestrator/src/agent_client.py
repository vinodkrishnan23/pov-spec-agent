"""Transport clients for invoking agents deployed in separate Magenta instances."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from contracts import AgentEnvelope, AgentRequest, AgentResponse, Artifact


class HTTPAgentClient:
    """Invoke a deployed Agent Engine agent with its dedicated service account."""

    token_url = "https://agentengine.mongodb.com/api/v1/oauth/token"

    def __init__(
        self,
        endpoint: str,
        client_id: str,
        client_secret: str,
        timeout_seconds: int = 300,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not endpoint:
            raise ValueError("A remote agent endpoint is required")
        if not client_id or not client_secret:
            raise ValueError("Remote agent client ID and client secret are required")
        self.endpoint = endpoint
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._access_token = ""
        self._token_expires_at = 0.0

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
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, transport=self.transport
        ) as client:
            token = await self._get_access_token(client)
            response = await client.post(
                self.endpoint,
                json={"message": json.dumps(payload)},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            value = self._response_value(response)
        if isinstance(value, str):
            value = json.loads(value)
        if isinstance(value, dict) and set(value) == {"result"}:
            value = value["result"]
            if isinstance(value, str):
                value = json.loads(value)
        if not isinstance(value, dict):
            raise TypeError("Remote agent response must be a JSON object")
        return value

    async def _get_access_token(self, client: httpx.AsyncClient) -> str:
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        response = await client.post(
            self.token_url,
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
        )
        response.raise_for_status()
        value = response.json()
        token = value.get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("Agent Engine OAuth response did not include access_token")
        self._access_token = token
        self._token_expires_at = time.time() + max(
            int(value.get("expires_in", 300)) - 30, 1
        )
        return token

    @staticmethod
    def _response_value(response: httpx.Response) -> Any:
        """Extract the final JSON value from Agent Engine JSON or SSE responses."""
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" not in content_type:
            return response.json()
        events = [
            line.removeprefix("data:").strip()
            for line in response.text.splitlines()
            if line.startswith("data:") and line.removeprefix("data:").strip() != "[DONE]"
        ]
        if not events:
            raise ValueError("Agent Engine stream did not include a data event")
        for event in reversed(events):
            try:
                value = json.loads(event)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise ValueError("Agent Engine stream did not include a JSON result")
