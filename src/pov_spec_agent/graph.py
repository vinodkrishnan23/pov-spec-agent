"""build_spec_graph — the "spec design" slice of pov-builder's new.md
pipeline, deployable standalone to Magenta.

Reuses pov-builder's own node/model/routing code as a dependency (see
pyproject.toml's `[tool.uv.sources]` pointing at `../pov-builder`) rather
than duplicating it — this project adds ONLY what's specific to running
this slice standalone: a truncated graph topology (ending after
`technical_spec_approval_gate` instead of fanning out to
seeder/backend_dev/frontend_dev) and the Magenta `App` wiring in main.py.
pov-builder itself is never modified.

    START
      |
transcript_analyzer <---------------------------------.
      |                                                |
  pov_reviewer --(FAIL)--> END                         |
      |(PASS/PASS_WITH_WARNINGS)                        |
  initial_spec_approval_gate --(revise)------------------'
      |(approve)
  spec_architect <----------------------------------.
      |                                              |
  technical_spec_approval_gate --(revise)-------------'
      |(approve)
     END

Everything past this point (code generation, integration validation,
repair, howto) stays with the full pov-builder pipeline — this deployment
covers new.md Phases 1-3 plus the two human approval gates only, per an
explicit product decision to deploy that slice on its own. The handoff
point is `pov_builder_runs`/the checkpointed `technical_spec` +
`spec_artifacts` (GitHub locations of the committed contracts) — whatever
runs code generation next reads from there, not from this process.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph
from pov_builder.graph import nodes, routing
from pov_builder.graph.state import POVState

NODE_NAMES = [
    "transcript_analyzer",
    "pov_reviewer",
    "initial_spec_approval_gate",
    "spec_architect",
    "technical_spec_approval_gate",
]


def _route_after_technical_spec_gate_spec_only(state: POVState) -> str:
    """Same decision `pov_builder.graph.routing.route_after_technical_spec_gate`
    makes, but this deployment ends the graph on approval instead of
    fanning out to seeder/backend_dev/frontend_dev — those nodes don't
    exist in this graph at all, so reusing the original function (which
    returns a list including their names) would be wrong here."""
    if state.get("technical_spec_decision") == "revise":
        return "spec_architect"
    return "end"


def build_spec_graph(llm, git_repo_store, pov_run_store, checkpointer=None):
    graph = StateGraph(POVState)

    graph.add_node("transcript_analyzer", nodes.make_transcript_analyzer(llm, pov_run_store))
    graph.add_node("pov_reviewer", nodes.make_pov_reviewer(llm))
    graph.add_node("initial_spec_approval_gate", nodes.initial_spec_approval_gate)
    graph.add_node("spec_architect", nodes.make_spec_architect(llm, git_repo_store, pov_run_store))
    graph.add_node("technical_spec_approval_gate", nodes.technical_spec_approval_gate)

    graph.set_entry_point("transcript_analyzer")
    graph.add_edge("transcript_analyzer", "pov_reviewer")

    graph.add_conditional_edges(
        "pov_reviewer",
        routing.route_after_pov_reviewer,
        {"initial_spec_approval_gate": "initial_spec_approval_gate", "end": END},
    )

    graph.add_conditional_edges(
        "initial_spec_approval_gate",
        routing.route_after_initial_spec_gate,
        {"spec_architect": "spec_architect", "transcript_analyzer": "transcript_analyzer"},
    )

    graph.add_edge("spec_architect", "technical_spec_approval_gate")

    graph.add_conditional_edges(
        "technical_spec_approval_gate",
        _route_after_technical_spec_gate_spec_only,
        {"spec_architect": "spec_architect", "end": END},
    )

    return graph.compile(checkpointer=checkpointer)
