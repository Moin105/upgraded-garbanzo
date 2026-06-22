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
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

# Defaults chosen for quality/cost: Sonnet 4.6 reasons well over retrieved
# code; cheaper Haiku is a good fallback for high-volume runs.
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

# Local / self-hosted models (Ollama, vLLM, LM Studio, llama.cpp, …) all speak
# the OpenAI API. Pointing karst at one keeps the ENTIRE loop on the machine —
# nothing leaves the network — which is what regulated/IP-sensitive teams need.
# Defaults target a stock Ollama install; override with KARST_LLM_* env vars.
DEFAULT_LOCAL_BASE_URL = "http://localhost:11434/v1"
DEFAULT_LOCAL_MODEL = "llama3.1"


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

    def __init__(
        self,
        model: str = DEFAULT_OPENAI_MODEL,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        provider: str = "openai",
        force_json: bool = True,
    ) -> None:
        from openai import OpenAI

        self.model = model
        self.provider = provider
        self._force_json = force_json
        # base_url / api_key let this same client talk to ANY OpenAI-compatible
        # endpoint — including a LOCAL model server (Ollama/vLLM/LM Studio), so
        # the request never leaves the machine.
        kwargs: dict[str, Any] = {}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        self._client = OpenAI(**kwargs)

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
        kwargs: dict[str, Any] = {}
        # JSON mode isn't universally supported by local OpenAI-compatible
        # servers, so it's opt-out for those — we still steer with the prompt
        # and parse defensively below.
        if self._force_json:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_with_schema},
                {"role": "user", "content": user},
            ],
            **kwargs,
        )
        raw = resp.choices[0].message.content or "{}"
        return _loads_json_lenient(raw)


def _loads_json_lenient(raw: str) -> dict[str, Any]:
    """Parse JSON from a model reply that may be wrapped in ```fences``` or
    surrounded by prose — common with local/open models that don't honour a
    strict JSON mode. Falls back to the first {...} block, then to {}."""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    start, end = raw.find("{"), raw.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def local_llm(model: str | None = None, base_url: str | None = None) -> LLM:
    """An OpenAI-compatible client pointed at a LOCAL model server (Ollama by
    default). Needs no real API key and makes no outbound internet calls — the
    whole ask loop stays on the machine, so IP-sensitive teams can use it."""
    url = base_url or os.environ.get("KARST_LLM_BASE_URL") or DEFAULT_LOCAL_BASE_URL
    # base_url is operator config (not caller input), but guard the scheme so a
    # typo/mistake can't point the client at file:// or another odd protocol.
    # We deliberately allow localhost/private hosts — that's the whole point.
    if not url.startswith(("http://", "https://")):
        raise ValueError("KARST_LLM_BASE_URL must start with http:// or https://")
    return OpenAILLM(
        model=model or os.environ.get("KARST_LLM_MODEL") or DEFAULT_LOCAL_MODEL,
        base_url=url,
        api_key=os.environ.get("KARST_LLM_API_KEY") or "local",
        provider="local",
        force_json=False,
    )


def default_llm(*, preferred: str | None = None, model: str | None = None) -> LLM:
    """Pick an LLM provider.

    Order: explicit `preferred` (or KARST_LLM_PROVIDER) → local endpoint
    (KARST_LLM_BASE_URL) → ANTHROPIC_API_KEY → OPENAI_API_KEY → raise.

    `local` routes to a self-hosted OpenAI-compatible server (Ollama/vLLM/…),
    keeping everything on-prem.
    """
    pref = (preferred or os.environ.get("KARST_LLM_PROVIDER") or "").lower()
    if pref == "local" or (not pref and os.environ.get("KARST_LLM_BASE_URL")):
        return local_llm(model=model)
    if pref == "anthropic" or (not pref and os.environ.get("ANTHROPIC_API_KEY")):
        return AnthropicLLM(model or DEFAULT_ANTHROPIC_MODEL)
    if pref == "openai" or (not pref and os.environ.get("OPENAI_API_KEY")):
        return OpenAILLM(model or DEFAULT_OPENAI_MODEL)
    raise LLMNotConfigured(
        "No LLM configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY for a cloud "
        "model, or KARST_LLM_PROVIDER=local (with Ollama/vLLM running) to stay "
        "fully on-prem — or run with --no-llm for retrieval-only output."
    )
