# pov-spec-agent

Magenta deployment of **pov-builder's spec-design slice** — new.md Phases
1–3 plus both human approval gates:

```
transcript -> InitialPOVSpec -> pov_reviewer -> initial_spec_approval_gate
  -> spec_architect (DetailedTechnicalSpec + GitHub-committed contracts)
  -> technical_spec_approval_gate -> END
```

Code generation (`seeder`/`backend_dev`/`frontend_dev`), integration
validation, repair, and `howto_helper` are **not** part of this
deployment — they remain in the full `pov-builder` pipeline, run
separately. The handoff point is the checkpointed `technical_spec` +
`spec_artifacts` (the real GitHub locations of every committed contract,
persisted to `pov_builder_runs` — see pov-builder's own README) —
whatever runs code generation next reads from there.

## Why a separate project instead of editing pov-builder

This was a deliberate choice, discussed before building: `pov-builder`
is a plain, non-Magenta LangGraph project (its own CLI + local FastAPI
UI). Rather than bolting Magenta deployment concerns onto it directly,
`pov-spec-agent` is a **separate project that depends on `pov-builder`**
(`pyproject.toml`'s `[tool.uv.sources]`, as a git dependency pinned to a
specific commit of https://github.com/vinodkrishnan23/pov-builder-code —
not a local relative path, so this repo is self-contained: a fresh
`git clone` of just `pov-spec-agent` builds on its own, with no sibling
checkout required) and adds only what's specific to running this slice
standalone on Magenta:

- `src/pov_spec_agent/graph.py` — `build_spec_graph(...)`, a truncated
  graph topology reusing `pov_builder.graph.nodes`/`routing` directly.
  The only genuinely new logic is one routing function
  (`_route_after_technical_spec_gate_spec_only`) that ends the graph on
  approval instead of fanning out to nodes that don't exist here.
- `src/pov_spec_agent/main.py` — the Magenta `App` wiring (mirrors
  `magenta-examples/agents/data-analyst-agent`'s confirmed-working
  pattern for a `framework: langgraph` agent using LangGraph's native
  `interrupt()`/`Command(resume=...)`, which both approval gates rely
  on).
- `agent.yaml` — `required_secrets` for exactly what this slice needs
  (LLM + `MONGODB_URI` + GitHub commit secrets); no `features.deep_agent`
  (this is a single graph, not `App.deep_agent()` subagent dispatch).

`pov-builder` itself is never modified by this project.

## Declared tools (Tool Pod), not wired into the graph

`src/pov_spec_agent/main.py` also declares 3 `@app.tool()` functions,
ported from `pov_builder.tools.git_push_tools` (which exists there purely
to show the tool shape — its own docstring says so): `resolve_pov_git_branch`,
`commit_spec_contract`, `commit_pov_code_files`. These run in Magenta's
**Tool Pod** — a separate process from AER — which is why `agent.yaml`
declares their secrets (`GITHUB_TOKEN`/`GITHUB_REPO`) a SECOND time under
`required_secrets.tools`, even though AER already has its own copy for
`spec_architect`'s direct calls.

Deliberately **not** wired into `build_spec_graph`'s LLM calls —
`spec_architect` still commits contracts deterministically via
`_git_repo_store` directly, exactly as before. This only makes the
capability *declared* (visible to the platform, independently invocable —
e.g. from the Playground's tool inspector), without changing the graph's
actual behavior. Turning them into something the graph's LLM genuinely
calls (via `bind_tools`/tool-calling in a node) would be a separate,
bigger change.

**Updating the pinned `pov-builder` commit** — bump the `rev` in
`pyproject.toml`'s `[tool.uv.sources]` to the new commit sha, then
`rm uv.lock && uv sync && uv run pytest`. Deliberately not left tracking
`main` automatically — a silent upstream change shouldn't change what
gets deployed here without a review step.

## `wheels/` — why 5 packages are vendored instead of fetched via git

`agentic build`'s remote sandbox has no way to authenticate a `git clone`
against a private repo — confirmed live: both `vinodkrishnan23/pov-builder-code`
and `10gen/magenta-client-libraries` are private, and a real build failed
with `fatal: could not read Username for 'https://github.com': terminal
prompts disabled`. There's no TTY for a credential prompt, and `.netrc`/
SSH keys/`.git-credentials` are unconditionally stripped from the uploaded
build archive. `agent.yaml`'s `artifact_repositories` mechanism doesn't
help either — its `type` field is a strict `pypi`/`npm` enum (checked
directly against the platform's own `agentconfig.ArtifactRepositoryConfig`
Go source); there's no `git` type. Locally, `uv sync` still works fine
because the local machine already has real git access — only the remote
build sandbox is blocked.

The platform's own documented fix for exactly this ("install private
Python packages at build time without exposing package-index credentials")
is vendoring pre-built wheels — `[tool.uv.sources]` now points
`pov-builder`, `agent-engine-sdk-langgraph`, and 3 of its own transitive
dependencies (`agent-engine-sdk`, `agent-engine-sdk-memory`,
`agent-engine-runner-shared` — normally resolved via `{ workspace = true }` *inside*
`magenta-client-libraries`' own monorepo, which doesn't apply once
installed standalone) at local `.whl` files in `wheels/`, instead of git
URLs. All 5 build with `hatchling` (pure Python, no compiled extensions),
so a wheel built on any platform works identically on the deploy target
(CPython 3.11 glibc Linux x86_64).

To rebuild these (e.g. after bumping a pinned commit):
```bash
# pov-builder
uv build --wheel --out-dir wheels /path/to/pov-builder-code/checkout

# magenta-client-libraries subpackages (adjust the checkout path to
# wherever `uv cache dir`/git-v0/checkouts/... has it, or a fresh clone)
for d in agent-engine-sdk-langgraph agent-engine-sdk agent-engine-sdk-memory agent-engine-runner-shared; do
  uv build --wheel --out-dir wheels /path/to/magenta-client-libraries/packages/python/packages/$d
done
```
Then `rm uv.lock && uv sync && uv run pytest` to confirm, and delete the
old `.whl` files these replace.

## Local development

```bash
uv sync
cp env.example .env   # fill in real values
agentic dev up
```

## Tests

Structural only (same honesty boundary as `pov-builder`'s own tests):
which nodes exist, and that the truncated graph genuinely ends after
`technical_spec_approval_gate` rather than falling through to code
generation. Whether the model's actual spec design is good needs a real
transcript + real LLM — see `pov-builder`'s own README.

```bash
uv run pytest
```

## Deploy

```bash
agentic secret set OPENAI_API_KEY
agentic secret set MONGODB_URI
agentic secret set GITHUB_TOKEN
agentic validate
agentic build
agentic deploy
```
