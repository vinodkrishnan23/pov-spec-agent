"""Identifier and artifact-version helpers."""

from __future__ import annotations

import re
import uuid

_ID_PATTERNS = {
    "poc": re.compile(r"^poc_[0-9a-f]{26}$"),
    "run": re.compile(r"^run_[0-9a-f]{26}$"),
    "task": re.compile(r"^task_[0-9a-f]{26}$"),
}
_VERSION_PATTERN = re.compile(r"^v(\d{3,})$")


def new_id(kind: str) -> str:
    """Create a sortable-enough opaque identifier with the required prefix."""
    if kind not in _ID_PATTERNS:
        raise ValueError(f"Unsupported identifier kind: {kind}")
    return f"{kind}_{uuid.uuid4().hex[:26]}"


def validate_id(value: str, kind: str) -> str:
    """Return a valid ID or raise a clear error."""
    pattern = _ID_PATTERNS.get(kind)
    if pattern is None:
        raise ValueError(f"Unsupported identifier kind: {kind}")
    if not pattern.fullmatch(value):
        raise ValueError(f"Invalid {kind}_id: {value}")
    return value


def next_version(current: str | None) -> str:
    """Return the next vNNN artifact version."""
    if current is None:
        return "v001"
    match = _VERSION_PATTERN.fullmatch(current)
    if not match:
        raise ValueError(f"Invalid artifact version: {current}")
    return f"v{int(match.group(1)) + 1:03d}"
