"""
app/llm/anthropic_provider.py

Anthropic Claude implementation of BaseLLMProvider.

Handles:
  - API calls via the official anthropic SDK
  - Exponential backoff retry on transient failures (tenacity)
  - Per-call timeout enforcement
  - JSON response parsing with fallback
  - Structured logging of every call (latency, retries, failures)
  - Clean error encapsulation — agents never see raw exceptions

MVP note: This uses the synchronous Anthropic client.
In a production system you would use the async client (anthropic.AsyncAnthropic)
to avoid blocking the FastAPI event loop during LLM calls.
For a portfolio demo on a laptop, synchronous is simpler and reliable.
"""

import json
import re
import time
from typing import Optional, Any

import anthropic
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
import logging

from app.llm.base import BaseLLMProvider, LLMResponse
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Pass tenacity's before_sleep logs through our logger
_tenacity_logger = logging.getLogger("tenacity")


# ── Exceptions we consider transient (worth retrying) ─────────────────────────
_RETRYABLE_EXCEPTIONS = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)


class AnthropicProvider(BaseLLMProvider):
    """
    Anthropic Claude provider.

    Creates one shared client instance. All calls go through
    complete() or complete_json() — no direct SDK usage outside this class.
    """

    def __init__(self):
        if not settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. "
                "Copy .env.example to .env and add your key."
            )
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.llm_timeout_seconds,
        )
        self._model = settings.llm_model
        logger.info(f"AnthropicProvider initialized — model: {self._model}")

    @property
    def model_name(self) -> str:
        return self._model

    # ── Core completion method ─────────────────────────────────────────────────

    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        """
        Call the Anthropic Messages API with retry logic.

        Retries up to LLM_MAX_RETRIES times on transient errors,
        with exponential backoff (2s → 4s → 8s).

        On persistent failure, returns a failed LLMResponse instead
        of raising — agents use the fallback path when success=False.
        """
        retry_count = 0
        start_time = time.monotonic()

        # Build the retry-decorated inner function here so we can
        # capture retry_count via closure
        attempt_results = {"retries": 0}

        @retry(
            stop=stop_after_attempt(settings.llm_max_retries + 1),
            wait=wait_exponential(multiplier=1, min=2, max=16),
            retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
            before_sleep=before_sleep_log(_tenacity_logger, logging.WARNING),
            reraise=True,
        )
        def _call_with_retry() -> str:
            attempt_results["retries"] += 1
            messages = [{"role": "user", "content": prompt}]
            kwargs: dict[str, Any] = {
                "model": self._model,
                "max_tokens": settings.llm_max_tokens,
                "messages": messages,
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            response = self._client.messages.create(**kwargs)
            return response.content[0].text

        try:
            content = _call_with_retry()
            latency_ms = int((time.monotonic() - start_time) * 1000)
            actual_retries = max(0, attempt_results["retries"] - 1)

            logger.info(
                "LLM call succeeded",
                extra={
                    "model": self._model,
                    "latency_ms": latency_ms,
                    "retry_count": actual_retries,
                    "prompt_chars": len(prompt),
                    "response_chars": len(content),
                },
            )

            return LLMResponse(
                content=content,
                model=self._model,
                success=True,
                retry_count=actual_retries,
                latency_ms=latency_ms,
            )

        except anthropic.AuthenticationError as e:
            # Don't retry auth errors — key is wrong
            latency_ms = int((time.monotonic() - start_time) * 1000)
            logger.error(f"LLM auth error — check ANTHROPIC_API_KEY: {e}")
            return LLMResponse(
                content="",
                model=self._model,
                success=False,
                retry_count=attempt_results["retries"] - 1,
                latency_ms=latency_ms,
                error=f"Authentication error: {str(e)}",
            )

        except Exception as e:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            actual_retries = max(0, attempt_results["retries"] - 1)
            logger.error(
                f"LLM call failed after {actual_retries} retries: {type(e).__name__}: {e}",
                extra={"model": self._model, "latency_ms": latency_ms},
            )
            return LLMResponse(
                content="",
                model=self._model,
                success=False,
                retry_count=actual_retries,
                latency_ms=latency_ms,
                error=f"{type(e).__name__}: {str(e)}",
            )

    # ── JSON completion method ─────────────────────────────────────────────────

    def complete_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        fallback: Optional[dict[str, Any]] = None,
    ) -> LLMResponse:
        """
        Call the LLM and parse the response as JSON.

        Strategy:
          1. Instruct the model to return only valid JSON (via system prompt).
          2. Call complete() with retry logic.
          3. Try to parse the response as JSON directly.
          4. If that fails, try to extract a JSON block from markdown fences.
          5. If all parsing fails, use the fallback dict and set success=False.

        Args:
            prompt:        The user prompt.
            system_prompt: Additional system instruction (combined with JSON directive).
            fallback:      Safe default dict used if parsing fails entirely.

        Returns:
            LLMResponse with parsed_json populated. success=False if fallback used.
        """
        _fallback = fallback or {}

        # Always prepend the JSON-output instruction to the system prompt
        json_system = _build_json_system_prompt(system_prompt)

        response = self.complete(prompt, system_prompt=json_system)

        if not response.success or not response.content.strip():
            logger.warning(
                "LLM call failed — using fallback JSON",
                extra={"error": response.error, "model": self._model},
            )
            response.parsed_json = _fallback
            return response

        # Attempt JSON parsing
        parsed = _try_parse_json(response.content)

        if parsed is not None:
            response.parsed_json = parsed
            return response

        # Parsing failed — use fallback
        logger.warning(
            "JSON parsing failed — using fallback",
            extra={
                "model": self._model,
                "raw_response_preview": response.content[:200],
            },
        )
        response.success = False
        response.error = "JSON parsing failed — fallback used"
        response.parsed_json = _fallback
        return response


# ── Helper functions ───────────────────────────────────────────────────────────

def _build_json_system_prompt(additional: Optional[str]) -> str:
    """
    Build the system prompt for JSON-output calls.

    Prepends a strict instruction to return only valid JSON.
    Any additional system prompt is appended after.
    """
    base = (
        "You must respond with valid JSON only. "
        "Do not include any explanation, markdown formatting, code fences, "
        "or text outside the JSON object. "
        "Your entire response must be parseable by json.loads()."
    )
    if additional:
        return f"{base}\n\n{additional}"
    return base


def _try_parse_json(text: str) -> Optional[dict[str, Any]]:
    """
    Attempt to parse a string as JSON, with fallback extraction strategies.

    Strategy order:
      1. Direct json.loads on stripped text
      2. Extract content from ```json ... ``` fences
      3. Extract content from ``` ... ``` fences
      4. Find the first {...} substring and parse that

    Returns the parsed dict, or None if all strategies fail.
    """
    text = text.strip()

    # Strategy 1: direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Strategy 2: strip ```json ... ``` markdown fences
    json_fence = re.search(r"```json\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if json_fence:
        try:
            result = json.loads(json_fence.group(1).strip())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # Strategy 3: strip generic ``` ... ``` fences
    generic_fence = re.search(r"```\s*([\s\S]*?)\s*```", text)
    if generic_fence:
        try:
            result = json.loads(generic_fence.group(1).strip())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # Strategy 4: find first {...} block
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        try:
            result = json.loads(brace_match.group(0))
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    return None


# ── Provider factory ───────────────────────────────────────────────────────────

def get_llm_provider() -> BaseLLMProvider:
    """
    Factory function that returns the configured LLM provider.
    Automatically uses Groq if GROQ_API_KEY is set (free).
    Falls back to Anthropic if ANTHROPIC_API_KEY is set.
    """
    from app.config import settings

    # Use Groq if key is present (free tier)
    if settings.groq_api_key and settings.groq_api_key != "":
        from app.llm.groq_provider import GroqProvider
        return GroqProvider()

    # Fall back to Anthropic
    return AnthropicProvider()
