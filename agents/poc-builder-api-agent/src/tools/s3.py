"""Versioned S3 artifact operations scoped to a POC prefix."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any


class S3ArtifactStore:
    def __init__(self, client: Any, bucket: str) -> None:
        if not bucket:
            raise ValueError("POC_ASSET_BUCKET is required")
        self.client = client
        self.bucket = bucket

    @staticmethod
    def validate_key(poc_id: str, key: str) -> str:
        prefix = f"pocs/{poc_id}/"
        if not key.startswith(prefix) or ".." in key.split("/"):
            raise ValueError(f"S3 key must stay under {prefix}")
        return key

    async def put_object(
        self,
        poc_id: str,
        run_id: str,
        key: str,
        body: bytes | str,
        content_type: str,
        producer: str,
    ) -> dict[str, Any]:
        self.validate_key(poc_id, key)
        payload = body.encode() if isinstance(body, str) else body
        digest = hashlib.sha256(payload).hexdigest()
        result = await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=payload,
            ContentType=content_type,
            Metadata={
                "poc_id": poc_id,
                "run_id": run_id,
                "producer": producer,
                "content_sha256": digest,
            },
            ServerSideEncryption="AES256",
        )
        return {"key": key, "version_id": result.get("VersionId"), "sha256": digest}

    async def get_object(self, poc_id: str, key: str) -> dict[str, Any]:
        self.validate_key(poc_id, key)
        result = await asyncio.to_thread(
            self.client.get_object, Bucket=self.bucket, Key=key
        )
        return {
            "body": result["Body"].read(),
            "metadata": result.get("Metadata", {}),
            "version_id": result.get("VersionId"),
        }

    async def create_presigned_get_url(
        self, poc_id: str, key: str, expires_in: int = 900
    ) -> str:
        self.validate_key(poc_id, key)
        if not 1 <= expires_in <= 604800:
            raise ValueError("expires_in must be between 1 and 604800 seconds")
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    async def list_prefix(self, poc_id: str, prefix: str) -> list[dict[str, Any]]:
        self.validate_key(poc_id, prefix)
        items: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            result = await asyncio.to_thread(self.client.list_objects_v2, **kwargs)
            items.extend(
                {
                    "key": item["Key"],
                    "size": item["Size"],
                    "last_modified": str(item["LastModified"]),
                }
                for item in result.get("Contents", [])
            )
            token = result.get("NextContinuationToken")
            if not token:
                return items

    async def delete_runtime_object(self, poc_id: str, key: str) -> None:
        self.validate_key(poc_id, key)
        relative = key.removeprefix(f"pocs/{poc_id}/")
        if not relative.startswith(("deploy/", "test/")):
            raise ValueError("Only deploy and test artifacts may be deleted")
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)
