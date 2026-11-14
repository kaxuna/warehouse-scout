"""Building the Claude API client, and what a call costs."""

import anthropic

from warehouse_scout.config import Settings

# US dollars per million tokens (input, output), from Anthropic's pricing page.
# Checked 2026-11-14. A model missing here gets no cost estimate, not a wrong one.
PRICE_PER_MILLION_TOKENS: dict[str, tuple[float, float]] = {
    "claude-fable-5-2": (8.00, 40.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


def make_client(settings: Settings) -> anthropic.Anthropic:
    """Build the API client.

    With a key in settings (read from `.env`), pass it explicitly. Without
    one, let the SDK look in the process environment itself.
    """
    if settings.anthropic_api_key is None:
        return anthropic.Anthropic()
    return anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())


def has_credentials(client: anthropic.Anthropic) -> bool:
    """True if the client found a key, a token or a credential provider."""
    return bool(client.api_key or client.auth_token or client.credentials)


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Cost in US dollars, or None if the model's price is unknown."""
    prices = PRICE_PER_MILLION_TOKENS.get(model)
    if prices is None:
        return None
    input_price, output_price = prices
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
