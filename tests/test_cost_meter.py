"""Regression tests for the report-driven `ask` polish (v0.2.6 test report):

- the token/cost meter must reflect the provider actually in play, not always
  quote Anthropic pricing;
- `KARST_OFFLINE=1` must flip the HuggingFace offline switches.
"""

from __future__ import annotations

import contextlib
import io
from types import SimpleNamespace

from karst import cli
from karst.embedder import _apply_offline_env


def _hits(n: int = 2):
    code = "def f():\n    return 1\n" * 5
    return [SimpleNamespace(chunk=SimpleNamespace(code=code)) for _ in range(n)]


def _meter(**kwargs) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        cli._print_token_meter(_hits(), "how does auth work", **kwargs)
    return buf.getvalue().strip()


def test_local_provider_shows_no_api_cost():
    out = _meter(provider_hint="local", model_hint="llama3.1")
    assert "no API cost" in out
    assert "llama3.1" in out
    assert "$" not in out  # never quote a dollar figure for a local model


def test_anthropic_provider_prices_with_anthropic():
    out = _meter(provider_hint="anthropic", model_hint="claude-sonnet-4-6")
    assert "anthropic:claude-sonnet-4-6" in out
    assert "$" in out


def test_openai_provider_prices_with_openai():
    out = _meter(provider_hint="openai", model_hint="gpt-4o")
    assert "openai:gpt-4o" in out
    assert "$" in out


def test_no_llm_figure_is_labelled_an_estimate():
    out = _meter(provider_hint="anthropic", model_hint=None, estimate=True)
    assert out.startswith("est. ")


def test_no_llm_with_local_provider_is_free():
    # A user who has KARST_LLM_PROVIDER=local must not be quoted Anthropic rates.
    out = _meter(provider_hint="local", model_hint=None, estimate=True)
    assert "no API cost" in out
    assert "$" not in out


def test_karst_offline_sets_hf_flags(monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    monkeypatch.setenv("KARST_OFFLINE", "1")
    _apply_offline_env()
    import os

    assert os.environ.get("HF_HUB_OFFLINE") == "1"
    assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"


def test_karst_offline_unset_is_noop(monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("KARST_OFFLINE", raising=False)
    _apply_offline_env()
    import os

    assert os.environ.get("HF_HUB_OFFLINE") is None
