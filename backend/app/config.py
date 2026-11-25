import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass(frozen=True)
class Config:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "postgresql://registry:registry@localhost:5433/registry"))
    discovery_mode: str = field(default_factory=lambda: _env("DISCOVERY_MODE", "mock"))  # "mock" | "aws"
    aws_regions: list[str] = field(default_factory=lambda: [r.strip() for r in _env("AWS_REGIONS", "us-east-1").split(",") if r.strip()])
    agent_log_prefix: str = field(default_factory=lambda: _env("AGENT_LOG_PREFIX", "/ai/agents/"))
    log_lookback_hours: int = field(default_factory=lambda: int(_env("LOG_LOOKBACK_HOURS", "720")))
    default_monthly_requests: int = field(default_factory=lambda: int(_env("DEFAULT_MONTHLY_REQUESTS", "10000")))
    auto_discover: bool = field(default_factory=lambda: _env("AUTO_DISCOVER", "true").lower() in ("1", "true", "yes"))
    cors_origins: list[str] = field(default_factory=lambda: [o.strip() for o in _env("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()])


config = Config()
