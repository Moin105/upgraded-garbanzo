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


# ---- exact (real-usage) metering ------------------------------------------
# v0.2.9: after a real call, the meter prints the provider's ACTUAL token usage
# (no "~", no "est."), priced at the model actually used — not a chars/4 guess.

def test_actual_usage_is_exact_and_per_model():
    out = _meter(
        provider_hint="anthropic", model_hint="claude-opus-4-8",
        actual_in=1234, actual_out=567,
    )
    assert "1,234 in + 567 out tok" in out      # exact counts, verbatim
    assert "~" not in out                        # not an approximation
    assert not out.startswith("est. ")           # a real, incurred charge
    assert "anthropic:claude-opus-4-8" in out    # priced at the model used
    # opus = $15/Mtok in, $75/Mtok out -> 1234*15e-6 + 567*75e-6
    assert "$0.0185 + $0.0425 = $0.0610" in out


def test_actual_usage_local_shows_both_counts_and_no_cost():
    out = _meter(
        provider_hint="local", model_hint="llama3.1",
        actual_in=100, actual_out=200,
    )
    assert "100 in + 200 out tokens" in out
    assert "no API cost" in out and "$" not in out


def test_partial_usage_falls_back_to_estimate():
    # Only an input count (some local server omits completion_tokens): must NOT
    # masquerade as exact — fall back to the "~" estimate path.
    out = _meter(
        provider_hint="anthropic", model_hint="claude-sonnet-4-6",
        actual_in=999, actual_out=None,
    )
    assert "~" in out and "999" not in out       # estimated, not the partial count


def test_exact_unknown_pricing_still_shows_output_count():
    # A real call to a model we have no price table for must still report BOTH
    # real token counts, not silently drop the output and say "input tokens".
    out = _meter(
        provider_hint="anthropic", model_hint="claude-unknown-9",
        actual_in=100, actual_out=50,
    )
    assert "100 in + 50 out tokens" in out
    assert "pricing unknown" in out and "input tokens" not in out


def test_negative_usage_is_clamped_not_negative():
    out = _meter(
        provider_hint="anthropic", model_hint="claude-opus-4-8",
        actual_in=-5, actual_out=-5,
    )
    assert "0 in + 0 out tok" in out
    assert "-5" not in out and "$-" not in out      # never a negative bill


def test_anthropic_generate_captures_real_usage():
    from karst.llm import AnthropicLLM

    llm = object.__new__(AnthropicLLM)           # bypass __init__ (no SDK/network)
    llm.provider, llm.model = "anthropic", "claude-sonnet-4-6"

    class _Block:  # one text block
        text = "the answer"

    class _Usage:
        input_tokens, output_tokens = 321, 88

    class _Resp:
        content = [_Block()]
        usage = _Usage()

    class _Msgs:
        @staticmethod
        def create(**_):
            return _Resp()

    class _Client:
        messages = _Msgs()

    llm._client = _Client()
    r = llm.generate("sys", "user")
    assert r.text == "the answer"
    assert r.input_tokens == 321 and r.output_tokens == 88


def test_openai_generate_captures_real_usage():
    from karst.llm import OpenAILLM

    llm = object.__new__(OpenAILLM)
    llm.provider, llm.model, llm._force_json = "openai", "gpt-4o", True

    class _Msg:
        content = "hi"

    class _Choice:
        message = _Msg()

    class _Usage:
        prompt_tokens, completion_tokens = 42, 7

    class _Resp:
        choices = [_Choice()]
        usage = _Usage()

    class _Completions:
        @staticmethod
        def create(**_):
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    llm._client = _Client()
    r = llm.generate("sys", "user")
    assert r.input_tokens == 42 and r.output_tokens == 7


def test_response_without_usage_block_is_none():
    # A local OpenAI-compatible server that returns no usage must not crash; the
    # fields stay None and the meter falls back to the estimate.
    from karst.llm import OpenAILLM

    llm = object.__new__(OpenAILLM)
    llm.provider, llm.model, llm._force_json = "local", "llama3.1", False

    class _Msg:
        content = "hi"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]
        usage = None

    class _Completions:
        @staticmethod
        def create(**_):
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    llm._client = _Client()
    r = llm.generate("sys", "user")
    assert r.input_tokens is None and r.output_tokens is None
