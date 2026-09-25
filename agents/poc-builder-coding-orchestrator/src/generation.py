"""Deterministic source generation and bundle assembly helpers."""

from __future__ import annotations

import io
import tarfile
from typing import Any


def repair_component(
    component: str, attempt: int, max_attempts: int = 3
) -> dict[str, Any]:
    """Accept a bounded component repair request for asynchronous execution."""
    if component not in {"seed", "backend", "frontend"}:
        return {"status": "failed", "error": {"code": "UNKNOWN_COMPONENT"}}
    if attempt >= max_attempts:
        return {"status": "failed", "error": {"code": "REPAIR_LIMIT_REACHED"}}
    return {"status": "started", "component": component, "attempt": attempt + 1}


def tar_bundle(files: dict[str, str]) -> bytes:
    """Create a deterministic in-memory gzip tar archive for generated files."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for path, body in files.items():
            payload = body.encode()
            info = tarfile.TarInfo(path)
            info.size = len(payload)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()
