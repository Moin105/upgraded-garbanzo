"""Token + cost meter.

Spec §21 says token spend is the single biggest driver of agent cost. The
meter has two jobs:

1. Estimate tokens for an assembled prompt BEFORE we call the LLM, so we
   never surprise the user with a bill.
2. Translate that estimate to a dollar figure using per-provider pricing.

We deliberately use a cheap rough heuristic (chars/4) by default rather
than depending on tiktoken. The error is ~20% on English code, which is
fine for budget visibility. If tiktoken happens to be installed (often
the case in Python projects), we use it for tighter estimates on OpenAI
models.

Pricing constants are explicit so a future provider change is a one-line
edit. Constants reflect the public list prices for the default tier as of
the time of writing; the agent prints them so the user can recalibrate.
"""

from __future__ import annotations

from dataclasses import dataclass

# Default characters-per-token heuristic for English/code mix.
DEFAULT_CHARS_PER_TOKEN = 4


# Per-1M-token list prices (USD). Updated quarterly; keep current.
# Source: provider pricing pages.
@dataclass(frozen=True)
class ModelPricing:
    name: str
    input_per_mtok: float
    output_per_mtok: float


# Anthropic Claude
PRICING_ANTHROPIC: dict[str, ModelPricing] = {
    "claude-opus-4-8":         ModelPricing("claude-opus-4-8",         15.0, 75.0),
    "claude-opus-4-7":         ModelPricing("claude-opus-4-7",         15.0, 75.0),
    "claude-sonnet-4-6":       ModelPricing("claude-sonnet-4-6",        3.0, 15.0),
    "claude-haiku-4-5-20251001": ModelPricing("claude-haiku-4-5-20251001", 1.0,  5.0),
    "claude-fable-5":          ModelPricing("claude-fable-5",           3.0, 15.0),
}

# OpenAI GPT-4o family
PRICING_OPENAI: dict[str, ModelPricing] = {
    "gpt-4o":         ModelPricing("gpt-4o",         2.50, 10.00),
    "gpt-4o-mini":    ModelPricing("gpt-4o-mini",    0.15,  0.60),
}


def estimate_tokens(text: str, *, chars_per_token: int = DEFAULT_CHARS_PER_TOKEN) -> int:
    """Cheap, fast token estimate. ~20% accurate, no model dependency."""
    if not text:
        return 0
    return max(1, len(text) // chars_per_token)


def estimate_tokens_tiktoken(text: str, *, model: str = "gpt-4o-mini") -> int:
    """More accurate count via tiktoken if installed; falls back gracefully."""
    try:
        import tiktoken
    except ImportError:
        return estimate_tokens(text)
    try:
        enc = tiktoken.encoding_for_model(model)
    except Exception:
        enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))


def lookup_pricing(provider: str, model: str) -> ModelPricing | None:
    table = {"anthropic": PRICING_ANTHROPIC, "openai": PRICING_OPENAI}.get(provider.lower())
    if table is None:
        return None
    return table.get(model)


@dataclass
class CostEstimate:
    provider: str
    model: str
    input_tokens: int
    estimated_output_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    # True when both token counts came from the provider's real API usage block
    # (via price_usage), so the figure is billable-accurate rather than a guess.
    # Controls whether we print the "~" approximation marker.
    exact: bool = False

    @property
    def total_usd(self) -> float:
        return self.input_cost_usd + self.output_cost_usd

    def render(self) -> str:
        approx = "" if self.exact else "~"
        return (
            f"{approx}{self.input_tokens:,} in + {approx}{self.estimated_output_tokens:,} out tok | "
            f"${self.input_cost_usd:.4f} + ${self.output_cost_usd:.4f} = "
            f"${self.total_usd:.4f} ({self.provider}:{self.model})"
        )


def estimate_cost(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    estimated_output_tokens: int = 500,
) -> CostEstimate | None:
    p = lookup_pricing(provider, model)
    if p is None:
        return None
    return CostEstimate(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        estimated_output_tokens=estimated_output_tokens,
        input_cost_usd=input_tokens * p.input_per_mtok / 1_000_000,
        output_cost_usd=estimated_output_tokens * p.output_per_mtok / 1_000_000,
    )


def price_usage(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> CostEstimate | None:
    """Price the *real* token usage a provider reported for a completed call.

    Same math as `estimate_cost`, but flagged `exact=True` so the meter shows
    actual billed tokens (no "~") rather than the chars/4 pre-call estimate.
    """
    p = lookup_pricing(provider, model)
    if p is None:
        return None
    return CostEstimate(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        estimated_output_tokens=output_tokens,
        input_cost_usd=input_tokens * p.input_per_mtok / 1_000_000,
        output_cost_usd=output_tokens * p.output_per_mtok / 1_000_000,
        exact=True,
    )
