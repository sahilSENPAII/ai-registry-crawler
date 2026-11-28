import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
# provider: the crawler's default model, then the models `cli evaluate` compares it with
LLM_MODELS = {
    "bedrock": ["au.anthropic.claude-haiku-5-5", "au.anthropic.claude-sonnet-5-5"],
    "gemini": ["gemini-3-flash-preview", "gemini-3.1-pro-preview"],
    "groq": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],  # 120b cleared the 0.05 agent F1 bar in docs/evaluation.md
}


def _dotenv() -> dict:
    lines = [line.strip() for line in ENV_FILE.read_text().splitlines()] if ENV_FILE.exists() else []
    return {k.strip(): v.strip().strip("\"'") for k, v in (line.split("=", 1) for line in lines
                                                           if "=" in line and not line.startswith("#"))}


DOTENV = _dotenv()


def _env(name: str, default: str) -> str:
    """The environment wins over backend/.env."""
    return os.environ.get(name, DOTENV.get(name, default))


def _llm_models() -> list[str]:
    return LLM_MODELS.get(_env("LLM_PROVIDER", "bedrock"), LLM_MODELS["bedrock"])


@dataclass(frozen=True)
class Config:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "postgresql://registry:registry@localhost:5433/registry"))
    discovery_mode: str = field(default_factory=lambda: _env("DISCOVERY_MODE", "mock"))  # "mock" | "aws"
    aws_regions: list[str] = field(default_factory=lambda: [r.strip() for r in _env("AWS_REGIONS", "us-east-1").split(",") if r.strip()])
    agent_log_prefix: str = field(default_factory=lambda: _env("AGENT_LOG_PREFIX", "/ai/agents/"))
    log_lookback_hours: int = field(default_factory=lambda: int(_env("LOG_LOOKBACK_HOURS", "720")))
    default_monthly_requests: int = field(default_factory=lambda: int(_env("DEFAULT_MONTHLY_REQUESTS", "10000")))
    auto_discover: bool = field(default_factory=lambda: _env("AUTO_DISCOVER", "true").lower() in ("1", "true", "yes"))
    llm_enabled: bool = field(default_factory=lambda: _env("LLM_ENABLED", "false").lower() in ("1", "true", "yes"))
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "bedrock"))  # "bedrock" | "gemini" | "groq"
    # on Bedrock, an inference profile in the first region of AWS_REGIONS; the default keeps requests in Australia
    llm_model_id: str = field(default_factory=lambda: _env("LLM_MODEL_ID", _llm_models()[0]))
    # `cli evaluate` compares these; the first should be llm_model_id, which the others must clearly beat
    llm_eval_models: list[str] = field(default_factory=lambda: [m.strip() for m in _env(
        "LLM_EVAL_MODELS", ",".join(_llm_models())).split(",") if m.strip()])
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY", ""), repr=False)
    gemini_base_url: str = field(default_factory=lambda: _env("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"))
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY", ""), repr=False)
    groq_base_url: str = field(default_factory=lambda: _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1"))
    llm_max_steps: int = field(default_factory=lambda: int(_env("LLM_MAX_STEPS", "12")))
    cors_origins: list[str] = field(default_factory=lambda: [o.strip() for o in _env("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()])


config = Config()
