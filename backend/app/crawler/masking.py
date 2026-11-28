"""Masks secret-looking values at collection time so they never reach Postgres or the dashboard. Names are kept."""
import re

MASK = "[masked]"
SECRET_TERMS = r"key|token|secret|passw(?:or)?d|credential|auth"
SECRET_NAME = re.compile(SECRET_TERMS, re.I)
SECRET_VALUE = re.compile(
    r"\bsk-[A-Za-z0-9_-]{16,}"                    # OpenAI / Anthropic API keys
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"             # AWS access key ids
    r"|\b[0-9a-fA-F]{32,}\b"                      # long hex
    r"|(?<![\w+/])(?=[A-Za-z0-9+/]*\d)(?=[A-Za-z0-9+/]*[A-Za-z])[A-Za-z0-9+/]{40,}={0,2}"  # long base64
)
URL_CREDENTIALS = re.compile(r"(\b[a-z][\w+.-]*://[^\s:/@]*:)[^\s@/]+(@)", re.I)
# NAME = "literal" or "name": "literal", where the name looks secret
SECRET_ASSIGNMENT = re.compile(rf"(\b[\w-]*(?:{SECRET_TERMS})[\w-]*[\"']?\s*[:=]\s*)([\"'])[^\"'\n]+\2", re.I)


def mask_text(text: str) -> str:
    text = URL_CREDENTIALS.sub(rf"\1{MASK}\2", text)
    text = SECRET_ASSIGNMENT.sub(rf"\1\2{MASK}\2", text)
    return SECRET_VALUE.sub(MASK, text)


def mask(value):
    """Masks a whole value: string values under a secret-looking key, and secret-looking text anywhere."""
    if isinstance(value, dict):
        return {k: MASK if isinstance(v, str) and SECRET_NAME.search(k) else mask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    return mask_text(value) if isinstance(value, str) else value
