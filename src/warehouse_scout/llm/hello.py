"""First call to the Claude API: one question, one answer, and what it cost.

    make hello

Asks Claude to rewrite one Oracle query from the synthetic workload for
PostgreSQL. No tools and no loop yet; this is the bare request/response that
everything later is built on, with the three numbers worth watching from day
one: tokens, latency, cost.

Needs ANTHROPIC_API_KEY in `.env` (see `.env.example`).
"""

import sys
import time
from dataclasses import dataclass
from typing import Final

import anthropic

from warehouse_scout.config import Settings, get_settings

# Room for the answer. Thinking tokens count against this limit too, so a
# small value can cut a response off before any visible text is produced.
MAX_TOKENS = 16_000

# How hard the model works on the request. "low" is enough for one small query
# and keeps this call fast and cheap.
EFFORT: Final = "low"

# US dollars per million tokens (input, output), from Anthropic's pricing page.
# Checked 2026-11-14. A model missing here gets no cost estimate, not a wrong one.
PRICE_PER_MILLION_TOKENS: dict[str, tuple[float, float]] = {
    "claude-fable-5-2": (8.00, 40.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

SYSTEM_PROMPT = (
    "You are Warehouse Scout, an assistant for data-warehouse migrations. "
    "You translate SQL between dialects and say plainly what you changed and why."
)

QUESTION = """\
Rewrite this Oracle query for PostgreSQL. Then list each Oracle-specific
construct you replaced, one line each.

SELECT c.c_customer_id, NVL(ca.ca_state, 'UNKNOWN') AS state
FROM customer c, customer_address ca
WHERE c.c_current_addr_sk = ca.ca_address_sk(+)
  AND c.c_birth_year > 1970
"""


@dataclass(frozen=True)
class CallResult:
    """What one model call produced and what it used."""

    text: str
    model: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    latency_seconds: float
    request_id: str | None


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


def ask(client: anthropic.Anthropic, model: str, system: str, question: str) -> CallResult:
    """Send one user message and collect the reply with its usage numbers."""
    started = time.perf_counter()
    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        output_config={"effort": EFFORT},
        messages=[{"role": "user", "content": question}],
    )
    latency_seconds = time.perf_counter() - started

    # The reply is a list of blocks (text, thinking, tool calls, ...), not a
    # string. Keep the text blocks and join them.
    text = "".join(block.text for block in response.content if block.type == "text")

    return CallResult(
        text=text,
        model=response.model,
        stop_reason=response.stop_reason,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        latency_seconds=latency_seconds,
        request_id=response._request_id,
    )


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Cost of one call in US dollars, or None if the model's price is unknown."""
    prices = PRICE_PER_MILLION_TOKENS.get(model)
    if prices is None:
        return None
    input_price, output_price = prices
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def format_report(result: CallResult) -> str:
    cost = estimate_cost_usd(result.model, result.input_tokens, result.output_tokens)
    cost_text = "unknown (model not in price table)" if cost is None else f"${cost:.5f}"
    return "\n".join(
        [
            f"model        {result.model}",
            f"stop reason  {result.stop_reason}",
            f"tokens       {result.input_tokens} in, {result.output_tokens} out",
            f"latency      {result.latency_seconds:.1f} s",
            f"cost         {cost_text}",
            f"request id   {result.request_id}",
        ]
    )


def main() -> int:
    settings = get_settings()
    client = make_client(settings)

    if not has_credentials(client):
        print(
            "No API key found. Add ANTHROPIC_API_KEY to .env (see .env.example).", file=sys.stderr
        )
        return 1

    # Most specific error first. AuthenticationError and RateLimitError are
    # subclasses of APIStatusError, so they must come before it; Python takes
    # the first `except` that matches.
    try:
        result = ask(client, settings.scout_model, SYSTEM_PROMPT, QUESTION)
    except anthropic.AuthenticationError:
        print("The API rejected the key. Check ANTHROPIC_API_KEY in .env.", file=sys.stderr)
        return 1
    except anthropic.RateLimitError:
        print("Rate limited, even after the SDK's retries. Try again later.", file=sys.stderr)
        return 1
    except anthropic.APIStatusError as error:
        print(f"API returned {error.status_code}: {error.message}", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError:
        print("Could not reach the API. Check the network connection.", file=sys.stderr)
        return 1

    if result.stop_reason == "refusal":
        print("The model declined this request.", file=sys.stderr)
    elif result.stop_reason == "max_tokens":
        print("Answer was cut off at the token limit.", file=sys.stderr)

    print(result.text)
    print()
    print(format_report(result))
    return 0 if result.stop_reason == "end_turn" else 1


if __name__ == "__main__":
    sys.exit(main())
