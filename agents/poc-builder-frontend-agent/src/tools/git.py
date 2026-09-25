"""GitHub-backed artifact storage and common agent tools."""

from __future__ import annotations

import asyncio
import base64
import json
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlparse

import httpx
from agent_engine_sdk_langgraph import App

from tools.s3 import S3ArtifactStore

if TYPE_CHECKING:
    from runtime import AgentRuntime


class GitHubArtifactStore:
    """Store each POC's artifacts on an isolated branch of a shared GitHub repository."""

    def __init__(
        self,
        token: str,
        repository_url: str,
        api_url: str = "https://api.github.com",
    ) -> None:
        if not token:
            raise ValueError("GITHUB_TOKEN is required for Git artifact storage")
        path = urlparse(repository_url).path.strip("/").removesuffix(".git")
        if len(path.split("/")) != 2:
            raise ValueError("GITHUB_REPO_LINK must identify a GitHub owner/repository")
        self.token = token
        self.repository = path
        self.repository_url = f"https://github.com/{path}"
        self.api_url = api_url.rstrip("/")
        self._branch_locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def branch_for(poc_id: str) -> str:
        return f"poc/{poc_id}"

    async def put(
        self,
        poc_id: str,
        run_id: str,
        key: str,
        body: bytes | str,
        content_type: str,
        producer: str,
    ) -> dict[str, Any]:
        del content_type
        if not key.startswith((f"pocs/{poc_id}/", "backend/", "frontend/")):
            raise ValueError("Artifact key must be POC-scoped or a component folder")
        branch = self.branch_for(poc_id)
        payload = body.encode() if isinstance(body, str) else body
        async with self._branch_locks.setdefault(branch, asyncio.Lock()):
            await self._ensure_branch(branch)
            existing = await self._request(
                "GET", self._contents_url(key), params={"ref": branch}
            )
            data: dict[str, Any] = {
                "message": f"{producer}: write {key} ({run_id})",
                "content": base64.b64encode(payload).decode(),
                "branch": branch,
            }
            if existing.status_code == 200:
                data["sha"] = existing.json()["sha"]
            elif existing.status_code != 404:
                existing.raise_for_status()
            response = await self._request("PUT", self._contents_url(key), json=data)
            response.raise_for_status()
        commit = response.json()["commit"]
        return {"key": key, "version_id": commit["sha"], "sha256": commit["sha"]}

    async def get_text(self, poc_id: str, key: str) -> str:
        S3ArtifactStore.validate_key(poc_id, key)
        response = await self._request(
            "GET", self._contents_url(key), params={"ref": self.branch_for(poc_id)}
        )
        if response.status_code == 404:
            raise KeyError(f"Artifact not found: {key}")
        response.raise_for_status()
        return base64.b64decode(response.json()["content"]).decode()

    async def get_url_text(self, url: str) -> str:
        """Read a specification asset from an authenticated GitHub blob URL."""
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc not in {
            "github.com",
            "raw.githubusercontent.com",
        }:
            raise ValueError("Specification asset URL must be an HTTPS GitHub URL")
        raw_url = url.replace("/blob/", "/raw/", 1)
        response = await self._request("GET", raw_url)
        response.raise_for_status()
        return response.text

    async def create_presigned_get_url(
        self, poc_id: str, key: str, expires_in: int = 900
    ) -> str:
        del expires_in
        S3ArtifactStore.validate_key(poc_id, key)
        response = await self._request(
            "GET", self._contents_url(key), params={"ref": self.branch_for(poc_id)}
        )
        if response.status_code == 404:
            raise KeyError(f"Artifact not found: {key}")
        response.raise_for_status()
        return str(response.json()["html_url"])

    async def list(self, poc_id: str, prefix: str) -> list[dict[str, Any]]:
        S3ArtifactStore.validate_key(poc_id, prefix)
        branch = self.branch_for(poc_id)
        response = await self._request(
            "GET",
            f"{self.api_url}/repos/{self.repository}/git/trees/{quote(branch, safe='')}",
            params={"recursive": "1"},
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return [
            {"key": item["path"], "size": item.get("size", 0), "last_modified": None}
            for item in response.json().get("tree", [])
            if item.get("type") == "blob"
            and str(item.get("path", "")).startswith(prefix)
        ]

    async def _ensure_branch(self, branch: str) -> None:
        ref_url = f"{self.api_url}/repos/{self.repository}/git/ref/heads/{quote(branch, safe='')}"
        response = await self._request("GET", ref_url)
        if response.status_code == 200:
            return
        if response.status_code != 404:
            response.raise_for_status()
        repository = await self._request(
            "GET", f"{self.api_url}/repos/{self.repository}"
        )
        repository.raise_for_status()
        default_branch = repository.json()["default_branch"]
        default_ref = await self._request(
            "GET",
            f"{self.api_url}/repos/{self.repository}/git/ref/heads/{default_branch}",
        )
        default_ref.raise_for_status()
        created = await self._request(
            "POST",
            f"{self.api_url}/repos/{self.repository}/git/refs",
            json={
                "ref": f"refs/heads/{branch}",
                "sha": default_ref.json()["object"]["sha"],
            },
        )
        if created.status_code not in {201, 422}:
            created.raise_for_status()

    def _contents_url(self, key: str) -> str:
        return f"{self.api_url}/repos/{self.repository}/contents/{quote(key, safe='/')}"

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            return await client.request(method, url, headers=headers, **kwargs)


def register(app: App, runtime: AgentRuntime) -> None:
    """Register common artifact operations backed by the configured store."""

    @app.tool(is_local=False)
    async def write_artifact(poc_id: str, run_id: str, key: str, content: str) -> str:
        """Write a UTF-8 artifact to the configured Git store under this POC's prefix."""
        return json.dumps(
            await runtime.artifacts.put(
                poc_id, run_id, key, content, "text/plain", "common_git_tool"
            )
        )

    @app.tool(is_local=False)
    async def read_artifact(poc_id: str, key: str) -> str:
        """Read a UTF-8 artifact from the configured Git store."""
        return await runtime.artifacts.get_text(poc_id, key)

    @app.tool(is_local=False)
    async def list_artifacts(poc_id: str, prefix: str) -> str:
        """List artifacts from the configured Git store below a POC-scoped prefix."""
        return json.dumps(await runtime.artifacts.list(poc_id, prefix))
