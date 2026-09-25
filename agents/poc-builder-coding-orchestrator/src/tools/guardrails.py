"""Static safety checks for generated application bundles."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

_RULES = {
    "destructive-root-delete": re.compile(r"rm\s+-rf\s+/(?:\s|$)"),
    "pipe-remote-script": re.compile(
        r"(?:curl|wget).{0,200}\|\s*(?:ba)?sh", re.IGNORECASE
    ),
    "reverse-shell": re.compile(r"\bnc\s+.*\s-e\s"),
    "literal-mongodb-uri": re.compile(r"mongodb(?:\+srv)?://[^\s'\"]+"),
    "aws-access-key": re.compile(r"AKIA[0-9A-Z]{16}"),
}


def scan_text(text: str, filename: str = "<text>") -> list[dict[str, object]]:
    violations: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in _RULES.items():
            if pattern.search(line):
                violations.append(
                    {
                        "file": filename,
                        "line": line_number,
                        "rule": rule,
                        "snippet": line[:200],
                    }
                )
    return violations


def scan_files(paths: Iterable[Path]) -> dict[str, object]:
    violations: list[dict[str, object]] = []
    for path in paths:
        try:
            violations.extend(scan_text(path.read_text(), str(path)))
        except UnicodeDecodeError:
            continue
    return {"ok": not violations, "violations": violations}
