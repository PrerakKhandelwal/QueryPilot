from __future__ import annotations


class SQLAgentError(Exception):
    """Base error for the NL2SQL agent."""


class GuardrailError(SQLAgentError):
    """SQL or request rejected by security / safety policy."""


class ExecutionError(SQLAgentError):
    """Database execution failed."""


class LLMError(SQLAgentError):
    """LLM provider failed or returned unusable output."""


class SchemaError(SQLAgentError):
    """Schema introspection or retrieval failed."""


class RateLimitError(SQLAgentError):
    """Caller exceeded request budget."""


class AuthError(SQLAgentError):
    """Missing or invalid API credentials."""
