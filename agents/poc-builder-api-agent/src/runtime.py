"""Shared runtime dependencies injected into agent-specific main and tool modules."""

from __future__ import annotations

from dataclasses import dataclass

import boto3

from artifacts import ArtifactStore, InMemoryArtifactStore, S3StoreAdapter
from config import Settings, settings
from tools.git import GitHubArtifactStore
from tools.metadata import (
    InMemoryMetadataRepository,
    MetadataRepository,
    MongoMetadataRepository,
)
from tools.s3 import S3ArtifactStore


@dataclass(frozen=True)
class AgentRuntime:
    repository: MetadataRepository
    artifacts: ArtifactStore
    settings: Settings


def build_runtime(config: Settings = settings) -> AgentRuntime:
    """Build persistent shared dependencies once per AER or Tool Pod process."""
    if config.metadata_backend == "memory":
        repository: MetadataRepository = InMemoryMetadataRepository()
    elif config.metadata_backend == "mongodb":
        if not config.platform_mongodb_uri:
            raise RuntimeError(
                "MONGODB_URI or PLATFORM_MONGODB_URI is required. "
                "Set POC_METADATA_BACKEND=memory only for tests."
            )
        repository = MongoMetadataRepository(
            config.platform_mongodb_uri,
            config.platform_database,
            poc_collection=config.poc_collection,
            search_index=config.poc_search_index,
        )
    else:
        raise RuntimeError("POC_METADATA_BACKEND must be mongodb or memory")

    artifacts: ArtifactStore
    if config.artifact_backend == "git":
        artifacts = GitHubArtifactStore(config.github_token, config.github_repo_link)
    elif config.artifact_backend == "s3":
        artifacts = S3StoreAdapter(
            S3ArtifactStore(
                boto3.client("s3", region_name=config.aws_region), config.asset_bucket
            )
        )
    elif config.artifact_backend == "memory":
        artifacts = InMemoryArtifactStore()
    else:
        raise RuntimeError("POC_ARTIFACT_BACKEND must be git, s3, or memory")
    return AgentRuntime(repository=repository, artifacts=artifacts, settings=config)
