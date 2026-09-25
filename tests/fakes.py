"""Minimal test doubles for THIS project's own tests.

Deliberately not imported from pov-builder's `tests/fakes.py` — that
module isn't part of the installed `pov_builder` wheel (only `src/
pov_builder` is, per pov-builder's own pyproject.toml), so it isn't
reachable as a dependency. Kept intentionally tiny: just enough for
`build_spec_graph` to compile, which only needs `llm.with_structured_output`
to exist at graph-BUILD time (several node factories call it immediately,
not lazily inside the returned closure) — node bodies themselves are never
invoked by `.compile()`, so `git_repo_store`/`pov_run_store` need no real
behavior for these structural tests.
"""

from __future__ import annotations

from typing import Any


class _FakeStructuredRunnable:
    def __init__(self, result: Any = None):
        self._result = result

    def invoke(self, messages) -> Any:
        return self._result


class FakeLLM:
    def with_structured_output(self, schema: type, **kwargs: Any) -> _FakeStructuredRunnable:
        return _FakeStructuredRunnable()
