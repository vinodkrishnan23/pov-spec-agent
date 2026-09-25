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
(`pyproject.toml`'s `[tool.uv.sources]`, as an editable local path
dependency) and adds only what's specific to running this slice
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
