"""The single source of model facts. Prices (USD per 1M tokens) and capability scores are illustrative,
not current list prices or benchmark results; check them before making real decisions."""

MODELS = {
    "anthropic.claude-3-opus-20240229-v1:0": {
        "provider": "anthropic", "hosting": "bedrock", "capability": 92, "speed": 1,
        "price_in": 15.00, "price_out": 75.00, "supports_tools": True},
    "anthropic.claude-3-5-sonnet-20240620-v1:0": {
        "provider": "anthropic", "hosting": "bedrock", "capability": 88, "speed": 2,
        "price_in": 3.00, "price_out": 15.00, "supports_tools": True},
    "anthropic.claude-3-sonnet-20240229-v1:0": {
        "provider": "anthropic", "hosting": "bedrock", "capability": 80, "speed": 2,
        "price_in": 3.00, "price_out": 15.00, "supports_tools": True},
    "anthropic.claude-3-haiku-20240307-v1:0": {
        "provider": "anthropic", "hosting": "bedrock", "capability": 68, "speed": 3,
        "price_in": 0.25, "price_out": 1.25, "supports_tools": True},
    "meta.llama3-1-70b-instruct-v1:0": {
        "provider": "meta", "hosting": "bedrock", "capability": 78, "speed": 2,
        "price_in": 0.72, "price_out": 0.72, "supports_tools": True},
    "meta.llama3-1-8b-instruct-v1:0": {
        "provider": "meta", "hosting": "bedrock", "capability": 55, "speed": 3,
        "price_in": 0.22, "price_out": 0.22, "supports_tools": False},
    "amazon.nova-pro-v1:0": {
        "provider": "amazon", "hosting": "bedrock", "capability": 76, "speed": 2,
        "price_in": 0.80, "price_out": 3.20, "supports_tools": True},
    "amazon.nova-lite-v1:0": {
        "provider": "amazon", "hosting": "bedrock", "capability": 62, "speed": 3,
        "price_in": 0.06, "price_out": 0.24, "supports_tools": True},
    "amazon.nova-micro-v1:0": {
        "provider": "amazon", "hosting": "bedrock", "capability": 50, "speed": 3,
        "price_in": 0.035, "price_out": 0.14, "supports_tools": False},
    "gpt-4o": {
        "provider": "openai", "hosting": "external_api", "capability": 88, "speed": 2,
        "price_in": 2.50, "price_out": 10.00, "supports_tools": True},
    "gpt-4o-mini": {
        "provider": "openai", "hosting": "external_api", "capability": 72, "speed": 3,
        "price_in": 0.15, "price_out": 0.60, "supports_tools": True},
}

# min_capability: the bar a model must meet; input/output_tokens: a typical request
TASK_PROFILES = {
    "conversational": {"min_capability": 60, "input_tokens": 800, "output_tokens": 300, "needs_tools": False},
    "summarization": {"min_capability": 65, "input_tokens": 4000, "output_tokens": 400, "needs_tools": False},
    "classification": {"min_capability": 50, "input_tokens": 500, "output_tokens": 10, "needs_tools": False},
    "rag": {"min_capability": 70, "input_tokens": 3000, "output_tokens": 500, "needs_tools": False},
    "agentic_tool_use": {"min_capability": 85, "input_tokens": 6000, "output_tokens": 1200, "needs_tools": True},
    "decision_support": {"min_capability": 80, "input_tokens": 2500, "output_tokens": 800, "needs_tools": False},
    "content_generation": {"min_capability": 65, "input_tokens": 1000, "output_tokens": 800, "needs_tools": False},
}
