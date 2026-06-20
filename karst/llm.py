"""LLM provider dispatch.

Phase 1 supports Anthropic Claude (default) and OpenAI. The provider is
selected by env vars, so the agent runs anywhere the user already has API
keys configured. If neither is set, callers can fall back to retrieval-only
mode (still useful — top-k chunks with citations).

Model IDs live here as constants so the swap to a future model is one edit.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

# Defaults chosen for quality/cost: Sonnet 4.6 reasons well over retrieved
# code; cheaper Haiku is a good fallback for high-volume runs.
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


class LLMNotConfigured(RuntimeError):
    """Raised when no LLM provider env var is set and the caller requested one."""


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str


class LLM(ABC):
    provider: str
    model: str

    @abstractmethod
    def generate(self, system: str, user: str, *, max_tokens: int = 1500) -> LLMResponse: ...

    @abstractmethod
    def generate_structured(
        self,
        system: str,
        user: str,
        *,
        schema: dict[str, Any],
        tool_name: str = "report",
        tool_description: str = "Report structured results.",
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Force the model to emit JSON matching `schema`.

        Returns the parsed object. Providers implement via their native
        structured-output channel (Anthropic tool_use, OpenAI JSON mode).
        """
        ...


class AnthropicLLM(LLM):
    provider = "anthropic"

    def __init__(self, model: str = DEFAULT_ANTHROPIC_MODEL) -> None:
        import anthropic

        self.model = model
        self._client = anthropic.Anthropic()

    def generate(self, system: str, user: str, *, max_tokens: int = 1500) -> LLMResponse:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts: list[str] = []
        for block in resp.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return LLMResponse(text="".join(parts), provider=self.provider, model=self.model)

    def generate_structured(
        self,
        system: str,
        user: str,
        *,
        schema: dict[str, Any],
        tool_name: str = "report",
        tool_description: str = "Report structured results.",
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        # Anthropic tool_use forces the model into the schema reliably.
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            tools=[
                {
                    "name": tool_name,
                    "description": tool_description,
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                payload = getattr(block, "input", None)
                if isinstance(payload, dict):
                    return payload
        return {}


class OpenAILLM(LLM):
    provider = "openai"

    def __init__(self, model: str = DEFAULT_OPENAI_MODEL) -> None:
        from openai import OpenAI

        self.model = model
        self._client = OpenAI()

    def generate(self, system: str, user: str, *, max_tokens: int = 1500) -> LLMResponse:
        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = resp.choices[0].message.content or ""
        return LLMResponse(text=text, provider=self.provider, model=self.model)

    def generate_structured(
        self,
        system: str,
        user: str,
        *,
        schema: dict[str, Any],
        tool_name: str = "report",
        tool_description: str = "Report structured results.",
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        # OpenAI JSON mode requires the word "json" in the prompt. Append the
        # schema so the model has a concrete shape to fill, even though JSON
        # mode itself doesn't validate against it.
        system_with_schema = (
            f"{system}\n\n"
            f"Respond with JSON only. The JSON must conform to this schema:\n"
            f"{json.dumps(schema)}\n"
        )
        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_with_schema},
                {"role": "user", "content": user},
            ],
        )
        raw = resp.choices[0].message.content or "{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}


def default_llm(*, preferred: str | None = None) -> LLM:
    """Pick an LLM provider based on env vars.

    Order: explicit `preferred` → ANTHROPIC_API_KEY → OPENAI_API_KEY → raise.
    """
    pref = (preferred or "").lower()
    if pref == "anthropic" or (not pref and os.environ.get("ANTHROPIC_API_KEY")):
        return AnthropicLLM()
    if pref == "openai" or (not pref and os.environ.get("OPENAI_API_KEY")):
        return OpenAILLM()
    raise LLMNotConfigured(
        "No LLM configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY, "
        "or run with --no-llm to get retrieval-only output."
    )
