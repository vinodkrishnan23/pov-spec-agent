"""Environment-only configuration for POC Builder."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    aws_region: str = os.getenv("AWS_REGION", "ap-south-1")
    asset_bucket: str = os.getenv("POC_ASSET_BUCKET", "")
    artifact_backend: str = os.getenv("POC_ARTIFACT_BACKEND", "git")
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    github_repo_link: str = os.getenv("GITHUB_REPO_LINK", "")
    subnet_id: str = os.getenv("POC_SUBNET_ID", "")
    security_group_id: str = os.getenv("POC_SECURITY_GROUP_ID", "")
    instance_profile: str = os.getenv("POC_INSTANCE_PROFILE", "poc-instance-role")
    instance_type: str = os.getenv("POC_INSTANCE_TYPE", "t3.medium")
    atlas_client_id: str = os.getenv("ATLAS_CLIENT_ID", "")
    atlas_client_secret: str = os.getenv("ATLAS_CLIENT_SECRET", "")
    atlas_project_id: str = os.getenv("ATLAS_PROJECT_ID", "")
    atlas_sandbox_cluster: str = os.getenv("ATLAS_SANDBOX_CLUSTER", "poc-sandbox")
    atlas_sandbox_admin_uri: str = os.getenv("ATLAS_SANDBOX_ADMIN_URI", "")
    atlas_region: str = os.getenv("ATLAS_REGION", "AP_SOUTH_1")
    platform_mongodb_uri: str = os.getenv(
        "PLATFORM_MONGODB_URI", os.getenv("MONGODB_URI", "")
    )
    platform_database: str = os.getenv(
        "POC_METADATA_DATABASE", os.getenv("PLATFORM_DATABASE", "poc_builder")
    )
    poc_collection: str = os.getenv("POC_METADATA_COLLECTION", "pocs")
    metadata_backend: str = os.getenv("POC_METADATA_BACKEND", "mongodb")
    poc_search_index: str = os.getenv("POC_SEARCH_INDEX", "poc_search")
    ui_owner_user_id: str = os.getenv("POC_UI_OWNER_USER_ID", "anonymous")
    ui_allowed_origins: str = os.getenv(
        "POC_UI_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174",
    )
    ui_playground_url: str = os.getenv("POC_UI_PLAYGROUND_URL", "http://localhost:3001")
    ui_max_attachment_bytes: int = int(
        os.getenv("POC_UI_MAX_ATTACHMENT_BYTES", "1048576")
    )
    presigned_url_ttl_seconds: int = int(
        os.getenv("POC_PRESIGNED_URL_TTL_SECONDS", "900")
    )


settings = Settings()
