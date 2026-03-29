"""
app/llm/__init__.py

Public interface for the LLM layer.
Agents import get_llm_provider() from here — never from the concrete provider.
"""

from app.llm.anthropic_provider import get_llm_provider
from app.llm.base import BaseLLMProvider, LLMResponse, MockLLMProvider

__all__ = ["get_llm_provider", "BaseLLMProvider", "LLMResponse", "MockLLMProvider"]
