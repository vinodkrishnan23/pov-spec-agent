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


@app.entrypoint
def build_agent():
    llm = app.llm(build_llm())
    return build_spec_graph(llm, _git_repo_store, _pov_run_store, checkpointer=app.checkpointer())


def main() -> None:
    logger.info("Starting pov-spec-agent (Magenta SDK)")
    app.run()


if __name__ == "__main__":
    main()
