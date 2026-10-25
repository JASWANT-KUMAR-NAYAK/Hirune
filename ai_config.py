
"""AI configuration for local Ollama and authenticated Ollama Cloud."""

import os
import urllib.parse
from pathlib import Path
from dataclasses import dataclass, replace

from dotenv import load_dotenv

# Load environment variables from the project .env file.
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class AIError(Exception):
    """Any AI-backend failure. Callers fall back to the observed-only report."""


@dataclass(frozen=True)
class AIConfig:
    provider: str = "ollama"
    host: str = "http://localhost:11434"
    model: str = None
    model_prefix: str = "gemma"
    timeout: int = 120
    allow_remote: bool = False
    api_key: str = None

    def with_model(self, model):
        return replace(self, model=model) if model else self


def load_config(env=os.environ):
    d = AIConfig()
    return AIConfig(
        provider=env.get("NAP_AI_PROVIDER", d.provider),
        host=env.get("NAP_AI_HOST", d.host),
        model=env.get("NAP_AI_MODEL") or None,
        model_prefix=env.get("NAP_AI_MODEL_PREFIX", d.model_prefix),
        timeout=int(env.get("NAP_AI_TIMEOUT", d.timeout)),
        allow_remote=env.get("NAP_AI_ALLOW_REMOTE") == "1",
        api_key=env.get("NAP_AI_API_KEY") or env.get("LLM_API_KEY") or None,
    )


def require_local(cfg):
    """Refuse non-loopback hosts for the local provider unless explicitly allowed."""
    host = urllib.parse.urlparse(cfg.host).hostname
    if host not in LOCAL_HOSTS and not cfg.allow_remote:
        raise AIError(
            f"Refusing non-local AI host '{cfg.host}'. "
            "Set NAP_AI_ALLOW_REMOTE=1 to allow it explicitly."
        )


def pick_model(available, cfg):
    if cfg.model:
        return cfg.model if cfg.model in available else None
    return next(
        (m for m in available if m.lower().startswith(cfg.model_prefix.lower())),
        None,
    )
