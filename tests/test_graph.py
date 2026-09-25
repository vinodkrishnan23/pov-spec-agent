"""Structural + adapter tests for `build_spec_graph` — same honesty
boundary as pov-builder's own tests: deterministic wiring (which nodes
exist, where the truncated graph actually ends, and that the chat-message
adapter nodes correctly translate to/from AER's default `messages`
contract) is unit-testable without a live LLM; whether the model's real
spec design is good needs a real transcript + real LLM, same as
pov-builder's own README says for the full pipeline.
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage
from pov_spec_agent.graph import NODE_NAMES, build_spec_graph, emit_response, parse_request

from tests.fakes import FakeLLM


def test_graph_compiles_with_exactly_the_seven_nodes():
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


def test_technical_spec_approval_routes_to_emit_response_on_approve():
    from pov_spec_agent.graph import _route_after_technical_spec_gate_spec_only

    assert _route_after_technical_spec_gate_spec_only({"technical_spec_decision": "approve"}) == "emit_response"
    assert _route_after_technical_spec_gate_spec_only({}) == "emit_response"


def test_technical_spec_approval_loops_back_to_spec_architect_on_revise():
    from pov_spec_agent.graph import _route_after_technical_spec_gate_spec_only

    assert (
        _route_after_technical_spec_gate_spec_only({"technical_spec_decision": "revise"}) == "spec_architect"
    )


def test_parse_request_extracts_fields_from_the_chat_messages_json_body():
    """Regression test for a real bug: AER's default chat contract wraps
    a caller's {"message": "..."} into {"messages": [HumanMessage(...)]}
    — without this node, that content was silently discarded and
    transcript/user_email/pov_name never got set."""
    state = {
        "messages": [
            HumanMessage(
                content=json.dumps(
                    {"transcript": "a very specific transcript", "user_email": "a@b.com", "pov_name": "demo"}
                )
            )
        ]
    }

    result = parse_request(state)

    assert result == {"transcript": "a very specific transcript", "user_email": "a@b.com", "pov_name": "demo"}


def test_parse_request_ignores_non_json_content_instead_of_raising():
    state = {"messages": [HumanMessage(content="Hello")]}

    assert parse_request(state) == {}


def test_parse_request_is_a_noop_with_no_messages():
    assert parse_request({}) == {}


def test_emit_response_reports_review_failure_as_the_ai_message():
    from pov_builder.models.review import POVReviewResult, ReviewStatus

    state = {"pov_id": "pov-1", "review_result": POVReviewResult(status=ReviewStatus.FAIL)}

    result = emit_response(state)

    payload = json.loads(result["messages"][0].content)
    assert payload["status"] == "review_failed"
    assert payload["pov_id"] == "pov-1"
    assert "review_result" in payload


def test_emit_response_reports_approved_technical_spec_as_the_ai_message():
    from pov_builder.models.technical_spec import DetailedTechnicalSpec

    state = {"pov_id": "pov-1", "technical_spec": DetailedTechnicalSpec()}

    result = emit_response(state)

    payload = json.loads(result["messages"][0].content)
    assert payload["status"] == "technical_spec_approved"
    assert "technical_spec" in payload


def test_emit_response_falls_back_to_no_result_when_nothing_populated():
    result = emit_response({})

    payload = json.loads(result["messages"][0].content)
    assert payload["status"] == "no_result"
