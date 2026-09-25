"""pov-spec-agent — Magenta deployment of pov-builder's spec-design slice
(new.md Phases 1-3 plus both human approval gates): transcript ->
InitialPOVSpec -> review -> human approval -> DetailedTechnicalSpec ->
human approval. Code generation (seeder/backend_dev/frontend_dev) and
everything after stays with the full pov-builder pipeline, run separately.

Reuses pov-builder's own node/model/tooling code (see pyproject.toml's
`[tool.uv.sources]`) — this file only adds the Magenta `App` wiring,
mirroring the confirmed-working pattern in
`magenta-examples/agents/data-analyst-agent/src/data_analyst_agent/main.py`
— the real precedent in this workspace for a `framework: langgraph` agent
that uses LangGraph's native `interrupt()`/`Command(resume=...)` for
human-in-the-loop, which both of this graph's approval gates rely on.
"""

from __future__ import annotations

import json
import logging
import sys

from dotenv import load_dotenv
from magenta_sdklanggraph import App
from pov_builder.llm import build_llm
from pov_builder.tools.git_repo import GitHubRepoStore
from pov_builder.tools.mongo_store import MongoPovRunStore

from pov_spec_agent.graph import build_spec_graph

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S", stream=sys.stdout
)
logger = logging.getLogger(__name__)
load_dotenv()

app = App(app_name="POV Spec Agent")

# GitHubRepoStore/MongoPovRunStore load their own settings from the same
# env vars pov-builder's local run.py/webapp already use (GITHUB_TOKEN,
# GITHUB_REPO, MONGODB_URI, ...) — see pov_builder.config.settings. The
# LangGraph checkpointer itself is Magenta's own (`app.checkpointer()`),
# not pov-builder's local MongoDBSaver wiring.
_git_repo_store = GitHubRepoStore()
_pov_run_store = MongoPovRunStore()


# ---------------------------------------------------------------------------
# Tools — ported from pov_builder.tools.git_push_tools, which exists there
# purely to show the tool shape (its own docstring: "This module exists
# purely to show the TOOL shape, for a future agentic (tool-calling)
# reimplementation"). Declaring them here as REAL `@app.tool()` functions
# is that promised next step — per that same module's note, "the identical
# function signature and docstring become an `@app.tool()` function
# instead of `@tool` — nothing else changes."
#
# Deliberately NOT wired into build_spec_graph's LLM calls — spec_architect
# still commits deterministically via `_git_repo_store` directly, exactly
# as before. These exist so the capability is DECLARED (visible to the
# platform, invocable independently, e.g. from the Playground) without
# changing the graph's current behavior at all. `@app.tool()` functions
# run in Magenta's Tool Pod (a separate process from AER) — see
# agent.yaml's `required_secrets.tools` for the secrets that pod needs,
# distinct from `required_secrets.aer`'s copy for the graph's own direct
# calls.
# ---------------------------------------------------------------------------
@app.tool()
def resolve_pov_git_branch(email: str, pov_name: str) -> dict:
    """Ensure the POV's branch exists on the one shared GitHub repo, creating it off the repo's default branch the first time this exact (email, pov_name) is seen.

    Call this ONCE per POV, before committing any spec_architect contract
    file — it's what makes every later commit for the SAME POV land on
    the SAME branch instead of creating a duplicate. Calling it again for
    a POV that already has a branch is a safe no-op.

    Args:
        email: The end user's email — half of the POV's durable git identity.
        pov_name: The end user's chosen name for this POV — the other half.

    Returns:
        A dict with `repo_name` (the shared repo's bare name), `repo_url`
        (its GitHub URL), and `branch` (the POV's own branch name, in the
        form `<email-slug>/<pov_name-slug>`).
    """
    repo_name, repo_url, branch = _git_repo_store.resolve_repo(email=email, pov_name=pov_name)
    return {"repo_name": repo_name, "repo_url": repo_url, "branch": branch}


@app.tool()
def commit_spec_contract(email: str, pov_name: str, filename: str, content_json: str) -> dict:
    """Commit one spec_architect contract file to `spec_architect/<filename>` on the POV's own branch, creating the branch first if it doesn't exist yet.

    This call IS the push — GitHub's Contents API creates the commit
    directly in one HTTP call. There is no separate local `git add` /
    `git commit` / `git push` sequence anywhere in this path.

    Args:
        email: The end user's email — must match what `resolve_pov_git_branch` was called with for this POV.
        pov_name: The end user's chosen POV name — must match `resolve_pov_git_branch`.
        filename: The bare file name to commit under `spec_architect/`, e.g. "data_model.json".
        content_json: The file's full content as a JSON string, already serialized.

    Returns:
        A dict with `path` (the committed path on the branch) and
        `commit_sha` (the real GitHub commit SHA for this write).
    """
    refs = _git_repo_store.commit_spec_contracts(email=email, pov_name=pov_name, contracts={filename: content_json})
    ref = refs[filename]
    return {"path": ref.path, "commit_sha": ref.commit_sha}


@app.tool()
def commit_pov_code_files(email: str, pov_name: str, files_json: str, message: str) -> dict:
    """Commit one or more generated code files (seed/backend/frontend) to the POV's own branch, creating the branch first if it doesn't exist yet.

    Used by seeder/backend_dev/frontend_dev in the full pov-builder
    pipeline today (as a direct `GitHubRepoStore.commit_code_files` call,
    not a tool call) to push whatever files they just generated under
    `seed/`, `backend/`, or `frontend/`.

    Args:
        email: The end user's email — must match what `resolve_pov_git_branch` was called with for this POV.
        pov_name: The end user's chosen POV name — must match `resolve_pov_git_branch`.
        files_json: A JSON object string mapping each relative path (e.g. "backend/server.js") to its full file content.
        message: The commit message.

    Returns:
        A dict with `commit_sha` — the sha of the last file committed in this call.
    """
    files: dict[str, str] = json.loads(files_json)
    commit_sha = _git_repo_store.commit_code_files(email=email, pov_name=pov_name, files=files, message=message)
    return {"commit_sha": commit_sha}


@app.entrypoint
def build_agent():
    llm = app.llm(build_llm())
    return build_spec_graph(llm, _git_repo_store, _pov_run_store, checkpointer=app.checkpointer())


def main() -> None:
    logger.info("Starting pov-spec-agent (Magenta SDK)")
    app.run()


if __name__ == "__main__":
    main()
