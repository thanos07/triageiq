"""
app/llm/base.py

Abstract base class for LLM providers.

Why an abstraction layer?
  - Agents call llm.complete(prompt) — they never import Anthropic directly.
  - Swapping to OpenAI / Gemini / Mistral = write one new file, change .env.
  - Unit tests can inject a MockLLMProvider that returns fixture data.
  - All retry, timeout, and fallback logic lives in the concrete provider,
    not scattered across agents.

To add a new provider:
  1. Create app/llm/openai_provider.py
  2. Subclass BaseLLMProvider
  3. Implement complete() and complete_json()
  4. Change get_llm_provider() below to return your new class
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class LLMResponse:
    """
    Standardized response object returned by every provider.

    Attributes:
        content:       The raw text response from the model.
        model:         Model identifier that was used.
        success:       Whether the call succeeded (False = fallback was used).
        retry_count:   How many retries occurred before success or failure.
        latency_ms:    Wall-clock time for the call in milliseconds.
        error:         Error message if success=False, else None.
        parsed_json:   Pre-parsed dict if complete_json() was used, else None.
    """
    content:     str
    model:       str
    success:     bool
    retry_count: int   = 0
    latency_ms:  int   = 0
    error:       Optional[str]  = None
    parsed_json: Optional[dict[str, Any]] = None


class BaseLLMProvider(ABC):
    """
    Abstract interface for LLM providers.

    All agents interact exclusively with this interface.
    The concrete provider (AnthropicProvider) is injected at runtime.
    """

    @abstractmethod
    def complete(self, prompt: str, system_prompt: Optional[str] = None) -> LLMResponse:
        """
        Send a completion request to the LLM.

        Args:
            prompt:        The user-facing prompt text.
            system_prompt: Optional system instruction prepended to the call.

        Returns:
            LLMResponse with content, success flag, latency, and retry count.
            Even on failure, returns a valid LLMResponse (never raises by default).
        """
        ...

    @abstractmethod
    def complete_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        fallback: Optional[dict[str, Any]] = None,
    ) -> LLMResponse:
        """
        Send a completion request and parse the response as JSON.

        The provider is responsible for instructing the model to return
        valid JSON and for parsing the response.

        Args:
            prompt:        The user-facing prompt.
            system_prompt: Optional system instruction.
            fallback:      Dict to use as parsed_json if parsing fails.
                           If None, parsed_json will be {} on failure.

        Returns:
            LLMResponse with parsed_json populated. success=False if parsing
            failed and the fallback was used.
        """
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the model identifier string (e.g. 'claude-3-5-haiku-20241022')."""
        ...


class MockLLMProvider(BaseLLMProvider):
    """
    In-memory mock provider for unit tests.

    Returns configurable fixture responses without making API calls.
    Set fixed_response to control what complete() returns.
    Set fixed_json to control what complete_json() returns.
    """

    def __init__(
        self,
        fixed_response: str = '{"result": "mock response"}',
        fixed_json: Optional[dict] = None,
        should_fail: bool = False,
    ):
        self._fixed_response = fixed_response
        self._fixed_json = fixed_json or {"result": "mock"}
        self._should_fail = should_fail

    @property
    def model_name(self) -> str:
        return "mock-model"

    def complete(self, prompt: str, system_prompt: Optional[str] = None) -> LLMResponse:
        if self._should_fail:
            return LLMResponse(
                content="",
                model=self.model_name,
                success=False,
                error="Mock provider configured to fail",
            )
        return LLMResponse(
            content=self._fixed_response,
            model=self.model_name,
            success=True,
            latency_ms=5,
        )

    def complete_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        fallback: Optional[dict] = None,
    ) -> LLMResponse:
        if self._should_fail:
            return LLMResponse(
                content="",
                model=self.model_name,
                success=False,
                error="Mock provider configured to fail",
                parsed_json=fallback or {},
            )
        return LLMResponse(
            content=str(self._fixed_json),
            model=self.model_name,
            success=True,
            latency_ms=5,
            parsed_json=self._fixed_json,
        )
