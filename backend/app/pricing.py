# Illustrative USD prices per 1M tokens (input, output); check the provider's price list before relying on them.
PRICES = {
    "anthropic.claude-3-5-sonnet-20240620-v1:0": (3.00, 15.00),
    "anthropic.claude-3-sonnet-20240229-v1:0": (3.00, 15.00),
    "anthropic.claude-3-haiku-20240307-v1:0": (0.25, 1.25),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}


def cost_usd(model, in_tokens, out_tokens):
    """None when the model has no price or there is no token usage."""
    if model not in PRICES or (in_tokens is None and out_tokens is None):
        return None
    price_in, price_out = PRICES[model]
    return round(((in_tokens or 0) * price_in + (out_tokens or 0) * price_out) / 1_000_000, 6)
