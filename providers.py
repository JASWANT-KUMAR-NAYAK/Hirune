"""Inference backends. A provider only does two things: list models, return JSON text for a prompt.

To add llama.cpp / Jan (OpenAI-compatible: GET /v1/models, POST /v1/chat/completions):
write a Provider subclass and add it to PROVIDERS. Nothing else changes.
"""
import json
import urllib.error
import urllib.request

from ai_config import AIError, require_local


class Provider:
    def __init__(self, cfg):
        require_local(cfg)  # every provider is local-only unless explicitly allowed
        self.cfg = cfg

    def list_models(self):
        raise NotImplementedError

    def chat_json(self, prompt, model):
        """Return the model's reply text (expected to be a JSON string)."""
        raise NotImplementedError

    def _request(self, path, payload=None):
        req = urllib.request.Request(
            self.cfg.host.rstrip("/") + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, OSError) as e:  # includes timeouts
            raise AIError(f"{self.cfg.provider} unreachable or timed out ({e}).")
        except json.JSONDecodeError:
            raise AIError(f"{self.cfg.provider} returned non-JSON.")


class OllamaProvider(Provider):
    def list_models(self):
        return [m["name"] for m in self._request("/api/tags").get("models", [])]

    def chat_json(self, prompt, model):
        out = self._request("/api/chat", {
            "model": model, "stream": False, "format": "json",
            "options": {"temperature": 0.2},
            "messages": [{"role": "user", "content": prompt}],
        })
        try:
            return out["message"]["content"]
        except (KeyError, TypeError):
            raise AIError("Unexpected reply shape from ollama.")


PROVIDERS = {"ollama": OllamaProvider}


def get_provider(cfg):
    try:
        return PROVIDERS[cfg.provider](cfg)
    except KeyError:
        raise AIError(f"Unknown AI provider '{cfg.provider}'. Available: {', '.join(PROVIDERS)}.")
