"""Local / self-hosted LLM support: provider routing + lenient JSON parsing.

The 'local' provider points the OpenAI client at a self-hosted, OpenAI-compatible
server (Ollama/vLLM), so the whole ask loop stays on the machine. These tests
don't hit the network — they only check selection + parsing."""
from __future__ import annotations

import pytest

from karst.llm import (
    DEFAULT_LOCAL_MODEL,
    LLMNotConfigured,
    _loads_json_lenient,
    default_llm,
)

_LLM_ENV = [
    "KARST_LLM_PROVIDER",
    "KARST_LLM_BASE_URL",
    "KARST_LLM_MODEL",
    "KARST_LLM_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
]


def _clear(monkeypatch):
    for k in _LLM_ENV:
        monkeypatch.delenv(k, raising=False)


def test_lenient_json_plain():
    assert _loads_json_lenient('{"a": 1}') == {"a": 1}


def test_lenient_json_fenced():
    assert _loads_json_lenient('```json\n{"ok": true}\n```') == {"ok": True}


def test_lenient_json_in_prose():
    # local models often wrap JSON in chatter
    assert _loads_json_lenient('Sure, here you go:\n{"x": 2}\nHope that helps!') == {"x": 2}


def test_lenient_json_garbage_returns_empty():
    assert _loads_json_lenient("no json at all") == {}


def test_default_llm_unconfigured_raises(monkeypatch):
    _clear(monkeypatch)
    with pytest.raises(LLMNotConfigured):
        default_llm()


def test_local_provider_selected_explicitly(monkeypatch):
    pytest.importorskip("openai")
    _clear(monkeypatch)
    llm = default_llm(preferred="local")
    assert llm.provider == "local"
    assert llm.model == DEFAULT_LOCAL_MODEL
    # points at a local server, not the OpenAI cloud
    assert "11434" in str(llm._client.base_url)


def test_local_provider_via_env(monkeypatch):
    pytest.importorskip("openai")
    _clear(monkeypatch)
    monkeypatch.setenv("KARST_LLM_PROVIDER", "local")
    monkeypatch.setenv("KARST_LLM_MODEL", "qwen2.5-coder")
    llm = default_llm()
    assert llm.provider == "local"
    assert llm.model == "qwen2.5-coder"


def test_base_url_env_implies_local(monkeypatch):
    pytest.importorskip("openai")
    _clear(monkeypatch)
    # Just setting a base URL (no provider) should route local.
    monkeypatch.setenv("KARST_LLM_BASE_URL", "http://127.0.0.1:8000/v1")
    llm = default_llm()
    assert llm.provider == "local"
    assert "127.0.0.1:8000" in str(llm._client.base_url)
