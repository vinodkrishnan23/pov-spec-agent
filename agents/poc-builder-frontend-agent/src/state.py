"""Serializable LangGraph state for the user-facing agent."""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class POCBuilderState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
