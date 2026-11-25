from app.recommend.catalog import MODELS


def cost_usd(model, in_tokens, out_tokens):
    """None when the model has no price or there is no token usage. Prices come from the model catalog."""
    if model not in MODELS or (in_tokens is None and out_tokens is None):
        return None
    m = MODELS[model]
    return round(((in_tokens or 0) * m["price_in"] + (out_tokens or 0) * m["price_out"]) / 1_000_000, 6)
