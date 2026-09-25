"""Artifact stores shared by all child agents."""

from __future__ import annotations

from typing import Any, Protocol

from tools.s3 import S3ArtifactStore


class ArtifactStore(Protocol):
    async def put(
        self,
        poc_id: str,
        run_id: str,
        key: str,
        body: bytes | str,
        content_type: str,
        producer: str,
    ) -> dict[str, Any]: ...

    async def get_text(self, poc_id: str, key: str) -> str: ...

    async def get_url_text(self, url: str) -> str: ...

    async def create_presigned_get_url(
        self, poc_id: str, key: str, expires_in: int = 900
    ) -> str: ...

    async def list(self, poc_id: str, prefix: str) -> list[dict[str, Any]]: ...


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.url_objects: dict[str, bytes] = {}

    async def put(
        self,
        poc_id: str,
        run_id: str,
        key: str,
        body: bytes | str,
        content_type: str,
        producer: str,
    ) -> dict[str, Any]:
        del run_id, content_type, producer
        if not key.startswith((f"pocs/{poc_id}/", "backend/", "frontend/")):
            raise ValueError("Artifact key must be POC-scoped or a component folder")
        payload = body.encode() if isinstance(body, str) else body
        self.objects[key] = payload
        return {"key": key, "version_id": "memory", "sha256": "memory"}

    async def get_text(self, poc_id: str, key: str) -> str:
        S3ArtifactStore.validate_key(poc_id, key)
        if key not in self.objects:
            raise KeyError(f"Artifact not found: {key}")
        return self.objects[key].decode()

    async def get_url_text(self, url: str) -> str:
        if url not in self.url_objects:
            raise KeyError(f"Git asset not found: {url}")
        return self.url_objects[url].decode()

    async def create_presigned_get_url(
        self, poc_id: str, key: str, expires_in: int = 900
    ) -> str:
        S3ArtifactStore.validate_key(poc_id, key)
        if key not in self.objects:
            raise KeyError(f"Artifact not found: {key}")
        if not 1 <= expires_in <= 604800:
            raise ValueError("expires_in must be between 1 and 604800 seconds")
        return f"memory://{key}?expires_in={expires_in}"

    async def list(self, poc_id: str, prefix: str) -> list[dict[str, Any]]:
        S3ArtifactStore.validate_key(poc_id, prefix)
        return [
            {"key": key, "size": len(value), "last_modified": None}
            for key, value in sorted(self.objects.items())
            if key.startswith(prefix)
        ]


class S3StoreAdapter:
    def __init__(self, store: S3ArtifactStore) -> None:
        self.store = store

    async def put(
        self,
        poc_id: str,
        run_id: str,
        key: str,
        body: bytes | str,
        content_type: str,
        producer: str,
    ) -> dict[str, Any]:
        return await self.store.put_object(
            poc_id, run_id, key, body, content_type, producer
        )

    async def get_text(self, poc_id: str, key: str) -> str:
        result = await self.store.get_object(poc_id, key)
        return result["body"].decode()

    async def get_url_text(self, url: str) -> str:
        raise ValueError(f"Git asset reads are unavailable for the S3 backend: {url}")

    async def create_presigned_get_url(
        self, poc_id: str, key: str, expires_in: int = 900
    ) -> str:
        return await self.store.create_presigned_get_url(poc_id, key, expires_in)

    async def list(self, poc_id: str, prefix: str) -> list[dict[str, Any]]:
        return await self.store.list_prefix(poc_id, prefix)
