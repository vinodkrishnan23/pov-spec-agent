"""Shared helpers for agent-specific Magenta tool modules."""

from __future__ import annotations

import json
from typing import Any

from contracts import AgentEnvelope, AgentRequest
from ids import new_id


def build_request(
    poc_id: str,
    run_id: str,
    caller: str,
    agent: str,
    tool: str,
    mode: str,
    params: dict[str, Any],
) -> AgentRequest:
    return AgentRequest(
        poc_id=poc_id,
        run_id=run_id,
        task_id=new_id("task"),
        trace_id=new_id("task"),
        caller=caller,
        agent=agent,
        tool=tool,
        mode=mode,
        params=params,
    )


def parse_envelope(value: str, expected_agent: str) -> AgentEnvelope:
    envelope = AgentEnvelope.from_dict(json.loads(value))
    if envelope.request.agent != expected_agent:
        raise ValueError(
            f"Envelope targets {envelope.request.agent}, expected {expected_agent}"
        )
    return envelope
