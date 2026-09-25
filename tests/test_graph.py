"""Structural tests for `build_spec_graph` — same honesty boundary as
pov-builder's own tests: deterministic wiring (which nodes exist, where
the truncated graph actually ends) is unit-testable without a live LLM;
whether the model's real spec design is good needs a real transcript +
real LLM, same as pov-builder's own README says for the full pipeline.
"""

from __future__ import annotations

from pov_spec_agent.graph import NODE_NAMES, build_spec_graph

from tests.fakes import FakeLLM


def test_graph_compiles_with_exactly_the_five_spec_nodes():
    graph = build_spec_graph(FakeLLM(), git_repo_store=object(), pov_run_store=object())

    node_names = set(graph.get_graph().nodes) - {"__start__", "__end__"}
    assert node_names == set(NODE_NAMES)


def test_graph_has_no_code_generation_nodes():
    """The entire point of this slice: seeder/backend_dev/frontend_dev/
    integration_validator/repair_router/howto_helper must NOT exist in
    this graph — that's the full pipeline's job, run separately."""
    graph = build_spec_graph(FakeLLM(), git_repo_store=object(), pov_run_store=object())

    node_names = set(graph.get_graph().nodes)
    for excluded in (
        "seeder",
        "backend_dev",
        "frontend_dev",
        "integration_validator",
        "repair_router",
        "howto_helper",
    ):
        assert excluded not in node_names


def test_technical_spec_approval_ends_the_graph_on_approve():
    from pov_spec_agent.graph import _route_after_technical_spec_gate_spec_only

    assert _route_after_technical_spec_gate_spec_only({"technical_spec_decision": "approve"}) == "end"
    assert _route_after_technical_spec_gate_spec_only({}) == "end"


def test_technical_spec_approval_loops_back_to_spec_architect_on_revise():
    from pov_spec_agent.graph import _route_after_technical_spec_gate_spec_only

    assert (
        _route_after_technical_spec_gate_spec_only({"technical_spec_decision": "revise"}) == "spec_architect"
    )
