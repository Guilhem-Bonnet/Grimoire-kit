"""Shared OpenTelemetry GenAI semantic-convention vocabulary.

Two exporters project Grimoire telemetry into OTel GenAI shape:
``grimoire.traces.ledger.TraceLedger`` (structured ``TraceRecord`` objects,
canonical ``traces.jsonl``) and ``grimoire.tools.blueprint_telemetry`` (raw
hook/task-flow events, the compatibility path the Studio replay reads). Each
owns its own span topology — one turns a run into a parent span with
``execute_tool`` children, the other turns a single event into one span — but
both must speak the same attribute names, or a backend ingesting both sees
two dialects for the same signal. This module is that single vocabulary.

Reference: OpenTelemetry ``semantic-conventions-genai`` (Development status
as of 2026-09 — nothing in the spec is stable yet), summarized in
``framework/agentic-industry-reference.md`` section 6.2.
"""

from __future__ import annotations

__all__ = [
    "ATTR_AGENT_NAME",
    "ATTR_CONVERSATION_ID",
    "ATTR_INPUT_TOKENS",
    "ATTR_OPERATION_NAME",
    "ATTR_OUTPUT_TOKENS",
    "ATTR_PROVIDER_NAME",
    "ATTR_REQUEST_MODEL",
    "ATTR_TOOL_CALL_ID",
    "ATTR_TOOL_NAME",
    "OP_CHAT",
    "OP_EXECUTE_TOOL",
    "OP_INVOKE_AGENT",
    "OTEL_EXPORT_SCHEMA_VERSION",
    "PROVIDER_NAME",
    "agent_span_name",
    "model_span_name",
    "tool_span_name",
]

#: Stamped on every document this kit exports as OTel GenAI. Bump when the
#: attribute set or span topology changes in a way a consuming backend
#: (Langfuse, Phoenix, Honeycomb...) needs to know about.
OTEL_EXPORT_SCHEMA_VERSION = "grimoire.otel.v2"

#: Replaces the deprecated ``gen_ai.system`` (B7).
ATTR_PROVIDER_NAME = "gen_ai.provider.name"
ATTR_OPERATION_NAME = "gen_ai.operation.name"
ATTR_CONVERSATION_ID = "gen_ai.conversation.id"
ATTR_AGENT_NAME = "gen_ai.agent.name"
ATTR_TOOL_NAME = "gen_ai.tool.name"
ATTR_TOOL_CALL_ID = "gen_ai.tool.call.id"
ATTR_REQUEST_MODEL = "gen_ai.request.model"
ATTR_INPUT_TOKENS = "gen_ai.usage.input_tokens"
ATTR_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

OP_CHAT = "chat"
OP_INVOKE_AGENT = "invoke_agent"
OP_EXECUTE_TOOL = "execute_tool"

#: Value carried by ``gen_ai.provider.name`` for every span this kit emits.
PROVIDER_NAME = "grimoire"


def agent_span_name(agent_name: str) -> str:
    """Span name for a run driven by a named agent: ``invoke_agent {agent}``."""
    return f"{OP_INVOKE_AGENT} {agent_name}" if agent_name else OP_INVOKE_AGENT


def model_span_name(operation: str, model: str) -> str:
    """Span name for a model-facing operation: ``{operation} {model}``."""
    return f"{operation} {model}" if model else operation


def tool_span_name(tool: str) -> str:
    """Span name for a tool-execution child span: ``execute_tool {tool}``."""
    return f"{OP_EXECUTE_TOOL} {tool}"
