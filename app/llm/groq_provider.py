"""
app/llm/groq_provider.py

Groq implementation of BaseLLMProvider.
Groq is free and very fast — perfect for students and portfolio demos.
Get a free API key at console.groq.com
"""

import json
import re
import time
from typing import Optional, Any

from groq import Groq
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
_tenacity_logger = logging.getLogger("tenacity")


class GroqProvider(BaseLLMProvider):
    """
    Groq LLM provider — free tier available at console.groq.com
    Uses Llama 3 models via Groq's fast inference API.
    """

    def __init__(self):
        api_key = settings.groq_api_key
        if not api_key:
            raise ValueError(
                "GROQ_API_KEY is not set in your .env file. "
                "Get a free key at console.groq.com"
            )
        self._client = Groq(api_key=api_key)
        self._model = settings.groq_model
        logger.info(f"GroqProvider initialized — model: {self._model}")

    @property
    def model_name(self) -> str:
        return self._model

    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        """Call Groq API with retry logic."""
        start_time = time.monotonic()
        attempt_results = {"retries": 0}

        @retry(
            stop=stop_after_attempt(settings.llm_max_retries + 1),
            wait=wait_exponential(multiplier=1, min=2, max=16),
            retry=retry_if_exception_type(Exception),
            before_sleep=before_sleep_log(_tenacity_logger, logging.WARNING),
            reraise=True,
        )
        def _call():
            attempt_results["retries"] += 1
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=settings.llm_max_tokens,
                temperature=0.3,
            )
            return response.choices[0].message.content

        try:
            content = _call()
            latency_ms = int((time.monotonic() - start_time) * 1000)
            actual_retries = max(0, attempt_results["retries"] - 1)

            logger.info(
                "Groq call succeeded",
                extra={
                    "model": self._model,
                    "latency_ms": latency_ms,
                    "retry_count": actual_retries,
                },
            )
            return LLMResponse(
                content=content,
                model=self._model,
                success=True,
                retry_count=actual_retries,
                latency_ms=latency_ms,
            )

        except Exception as e:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            actual_retries = max(0, attempt_results["retries"] - 1)
            logger.error(f"Groq call failed: {type(e).__name__}: {e}")
            return LLMResponse(
                content="",
                model=self._model,
                success=False,
                retry_count=actual_retries,
                latency_ms=latency_ms,
                error=f"{type(e).__name__}: {str(e)}",
            )

    def complete_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        fallback: Optional[dict[str, Any]] = None,
    ) -> LLMResponse:
        """Call Groq and parse response as JSON."""
        _fallback = fallback or {}
        json_system = (
            "You must respond with valid JSON only. "
            "Do not include any explanation, markdown formatting, code fences, "
            "or text outside the JSON object. "
            "Your entire response must be parseable by json.loads()."
        )
        if system_prompt:
            json_system = f"{json_system}\n\n{system_prompt}"

        response = self.complete(prompt, system_prompt=json_system)

        if not response.success or not response.content.strip():
            response.parsed_json = _fallback
            return response

        parsed = self._try_parse_json(response.content)
        if parsed is not None:
            response.parsed_json = parsed
            return response

        logger.warning("JSON parsing failed — using fallback")
        response.success = False
        response.error = "JSON parsing failed — fallback used"
        response.parsed_json = _fallback
        return response

    def _try_parse_json(self, text: str) -> Optional[dict]:
        text = text.strip()
        # Direct parse
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
        # Strip ```json fences
        fence = re.search(r"```json\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if fence:
            try:
                result = json.loads(fence.group(1).strip())
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass
        # Find first {...}
        brace = re.search(r"\{[\s\S]*\}", text)
        if brace:
            try:
                result = json.loads(brace.group(0))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass
        return None