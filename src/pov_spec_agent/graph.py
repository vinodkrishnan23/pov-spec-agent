"""build_spec_graph — the "spec design" slice of pov-builder's new.md
pipeline, deployable standalone to Magenta.

Reuses pov-builder's own node/model/routing code as a dependency (see
pyproject.toml's `[tool.uv.sources]`) rather than duplicating it — this
project adds ONLY what's specific to running this slice standalone: a
truncated graph topology, the chat-message adapter nodes below, and the
Magenta `App` wiring in main.py. pov-builder itself is never modified.

    START
      |
  parse_request                  (reads the AER chat message's JSON body
      |                           into transcript/user_email/pov_name)
transcript_analyzer <---------------------------------.
      |                                                |
  pov_reviewer --(FAIL)--> emit_response --> END       |
      |(PASS/PASS_WITH_WARNINGS)                        |
  initial_spec_approval_gate --(revise)------------------'
      |(approve)
  spec_architect <----------------------------------.
      |                                              |
  technical_spec_approval_gate --(revise)-------------'
      |(approve)
  emit_response --> END

## Why `parse_request`/`emit_response` exist

Magenta's AER `/invoke` endpoint has a default CHAT contract: a caller's
`{"message": "..."}` gets wrapped into `{"messages": [HumanMessage(...)]}`,
and the response the caller sees is extracted from the LAST AI message in
`result["messages"]`. `pov_builder.graph.state.POVState` has no `messages`
key at all (it's a custom pipeline state, not a chat state) — so without
these two nodes, any caller's message is silently discarded (never reaches
`transcript`/`user_email`/`pov_name`) and the response is always empty
("Graph completed without producing messages", hit live against a real
deployed instance).

`pov-generator/spec-architect` (a different, already-working Magenta agent
in this workspace) solves the exact same mismatch the exact same way —
`parse_request`/`emit_response` here mirror its pattern:
- `parse_request` (new entry point): JSON-parses the incoming message's
  content as `{"transcript": ..., "user_email": ..., "pov_name": ...}`
  and merges those into state.
- `emit_response` (replaces every `END` edge): sets `messages` to a single
  AIMessage whose content is `json.dumps(...)` of whatever's relevant —
  `review_result` on an early FAIL, or `technical_spec` + `repository` on
  approval — so AER's response extraction has something to find.

`SpecAgentState` extends `POVState` with just the `messages` field needed
for this adapter — declared HERE, not in `pov_builder.graph.state`, so
pov-builder's own state/graph stay exactly as they are.

Everything past `technical_spec_approval_gate` (code generation,
integration validation, repair, howto) stays with the full pov-builder
pipeline — this deployment covers new.md Phases 1-3 plus the two human
approval gates only, per an explicit product decision to deploy that
slice on its own. The handoff point is `pov_builder_runs`/the checkpointed
`technical_spec` + `spec_artifacts` (GitHub locations of the committed
contracts) — whatever runs code generation next reads from there, not
from this process.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from pov_builder.graph import nodes, routing
from pov_builder.graph.state import POVState
from pov_builder.models.review import ReviewStatus

NODE_NAMES = [
    "parse_request",
    "transcript_analyzer",
    "pov_reviewer",
    "initial_spec_approval_gate",
    "spec_architect",
    "technical_spec_approval_gate",
    "emit_response",
]


class SpecAgentState(POVState, total=False):
    messages: Annotated[list, add_messages]


def _route_after_technical_spec_gate_spec_only(state: SpecAgentState) -> str:
    """Same decision `pov_builder.graph.routing.route_after_technical_spec_gate`
    makes, but this deployment ends the graph on approval instead of
    fanning out to seeder/backend_dev/frontend_dev — those nodes don't
    exist in this graph at all, so reusing the original function (which
    returns a list including their names) would be wrong here."""
    if state.get("technical_spec_decision") == "revise":
        return "spec_architect"
    return "emit_response"


def parse_request(state: SpecAgentState) -> dict:
    """Entry node — see module docstring. Best-effort: a message that
    isn't a JSON object is simply ignored (falls through to
    `transcript_analyzer`'s own empty-transcript short-circuit) rather
    than raising and failing the whole invocation."""
    messages = state.get("messages") or []
    if not messages:
        return {}
    content = getattr(messages[-1], "content", "")
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: payload[key] for key in ("transcript", "user_email", "pov_name", "pov_id") if key in payload}


def emit_response(state: SpecAgentState) -> dict:
    """Exit node (replaces every direct `END` edge) — see module
    docstring. Reports whichever stage the run actually reached, as the
    full JSON dump of the relevant state (not a hand-picked subset), so a
    caller can parse the exact same shape pov-builder's own checkpoint
    would show."""
    pov_id = state.get("pov_id")
    review_result = state.get("review_result")
    technical_spec = state.get("technical_spec")
    initial_spec = state.get("initial_spec")

    payload: dict[str, Any] = {"pov_id": pov_id}
    if review_result is not None and review_result.status == ReviewStatus.FAIL:
        payload["status"] = "review_failed"
        payload["review_result"] = review_result.model_dump(mode="json")
    elif technical_spec is not None:
        payload["status"] = "technical_spec_approved"
        payload["technical_spec"] = technical_spec.model_dump(mode="json")
        repository = state.get("repository")
        if repository is not None:
            payload["repository"] = repository.model_dump(mode="json")
    elif initial_spec is not None:
        payload["status"] = "no_transcript_or_incomplete"
        payload["initial_spec"] = initial_spec.model_dump(mode="json")
    else:
        payload["status"] = "no_result"

    return {"messages": [AIMessage(content=json.dumps(payload))]}


def build_spec_graph(llm, git_repo_store, pov_run_store, checkpointer=None):
    graph = StateGraph(SpecAgentState)

    graph.add_node("parse_request", parse_request)
    graph.add_node("transcript_analyzer", nodes.make_transcript_analyzer(llm, pov_run_store))
    graph.add_node("pov_reviewer", nodes.make_pov_reviewer(llm))
    graph.add_node("initial_spec_approval_gate", nodes.initial_spec_approval_gate)
    graph.add_node("spec_architect", nodes.make_spec_architect(llm, git_repo_store, pov_run_store))
    graph.add_node("technical_spec_approval_gate", nodes.technical_spec_approval_gate)
    graph.add_node("emit_response", emit_response)

    graph.set_entry_point("parse_request")
    graph.add_edge("parse_request", "transcript_analyzer")
    graph.add_edge("transcript_analyzer", "pov_reviewer")

    graph.add_conditional_edges(
        "pov_reviewer",
        routing.route_after_pov_reviewer,
        {"initial_spec_approval_gate": "initial_spec_approval_gate", "end": "emit_response"},
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
        {"spec_architect": "spec_architect", "emit_response": "emit_response"},
    )

    graph.add_edge("emit_response", END)

    return graph.compile(checkpointer=checkpointer)
