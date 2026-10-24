"""All AI configuration and model selection live here. Nothing else reads env vars or hosts.

Env vars (all optional):
  NAP_AI_PROVIDER      provider name, default "ollama"
  NAP_AI_HOST          base URL, default http://localhost:11434
  NAP_AI_MODEL         exact model name; if unset, first installed model starting with NAP_AI_MODEL_PREFIX
  NAP_AI_MODEL_PREFIX  default "gemma"
  NAP_AI_TIMEOUT       seconds, default 120
  NAP_AI_ALLOW_REMOTE  set to 1 to explicitly allow a non-local host (off by default)
"""
import os
import urllib.parse
from dataclasses import dataclass, replace

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
    )


def require_local(cfg):
    """Refuse non-loopback hosts unless explicitly allowed. Applies to every provider."""
    host = urllib.parse.urlparse(cfg.host).hostname
    if host not in LOCAL_HOSTS and not cfg.allow_remote:
        raise AIError(f"Refusing non-local AI host '{cfg.host}'. Set NAP_AI_ALLOW_REMOTE=1 to allow it explicitly.")


def pick_model(available, cfg):
    """Configured model if installed; else first installed model matching the prefix; else None.
    A missing configured model is never silently swapped for another."""
    if cfg.model:
        return cfg.model if cfg.model in available else None
    return next((m for m in available if m.lower().startswith(cfg.model_prefix.lower())), None)
