import re

from app.recommend.catalog import MODELS

# The crawler's own models are priced here rather than in the catalog, so they are never recommended to a system.
# List prices from Anthropic (Bedrock geo inference profiles can cost slightly more), Google AI Studio's paid tier for
# prompts up to 200k tokens, and Groq; output includes thinking or reasoning tokens.
CRAWLER_MODELS = {"anthropic.claude-haiku-5-5": {"price_in": 0.10, "price_out": 0.50},
                  "anthropic.claude-sonnet-5-5": {"price_in": 2.00, "price_out": 10.00},
                  "gemini-3-flash-preview": {"price_in": 0.50, "price_out": 3.00},
                  "gemini-3.1-pro-preview": {"price_in": 2.00, "price_out": 12.00},
                  "openai/gpt-oss-20b": {"price_in": 0.075, "price_out": 0.30},
                  "openai/gpt-oss-120b": {"price_in": 0.15, "price_out": 0.60}}
PROFILE_PREFIX = re.compile(r"^(us|eu|apac|au|jp|global)\.")


def cost_usd(model, in_tokens, out_tokens):
    """None when the model has no price or there is no token usage. Prices come from the model catalog."""
    m = MODELS.get(model) or CRAWLER_MODELS.get(PROFILE_PREFIX.sub("", model or ""))
    if m is None or (in_tokens is None and out_tokens is None):
        return None
    return round(((in_tokens or 0) * m["price_in"] + (out_tokens or 0) * m["price_out"]) / 1_000_000, 6)
