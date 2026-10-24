"""Inference backends for local Ollama and Ollama Cloud."""
import json
import urllib.error
import urllib.request

from ai_config import AIError, require_local

class Provider:
    def __init__(self, cfg):
        self.cfg = cfg
    def list_models(self):
        raise NotImplementedError
    def chat_json(self, prompt, model):
        raise NotImplementedError

class OllamaProvider(Provider):
    """Local Ollama native API; never sends data off-device."""
    def __init__(self, cfg):
        require_local(cfg)
        super().__init__(cfg)
    def _request(self, path, payload=None):
        req = urllib.request.Request(self.cfg.host.rstrip("/") + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, OSError) as e:
            raise AIError(f"{self.cfg.provider} unreachable or timed out ({e}).")
        except json.JSONDecodeError:
            raise AIError(f"{self.cfg.provider} returned non-JSON.")
    def list_models(self):
        return [m["name"] for m in self._request("/api/tags").get("models", [])]
    def chat_json(self, prompt, model):
        out = self._request("/api/chat", {"model": model, "stream": False, "format": "json",
            "options": {"temperature": 0.2}, "messages": [{"role": "user", "content": prompt}]})
        try:
            return out["message"]["content"]
        except (KeyError, TypeError):
            raise AIError("Unexpected reply shape from local Ollama.")

class OllamaCloudProvider(Provider):
    """Ollama Cloud's OpenAI-compatible API. Requires explicit provider selection and API key."""
    def __init__(self, cfg):
        super().__init__(cfg)
        if not cfg.api_key:
            raise AIError("Ollama Cloud API key missing. Set NAP_AI_API_KEY or LLM_API_KEY.")
        # Pin the cloud host so credentials cannot accidentally be sent to another server.
        from urllib.parse import urlparse
        parsed = urlparse(cfg.host)
        if parsed.scheme != "https" or parsed.hostname != "ollama.com":
            raise AIError("Ollama Cloud host must be https://ollama.com/v1.")
    def _request(self, path, payload=None):
        req = urllib.request.Request(self.cfg.host.rstrip("/") + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.cfg.api_key})
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            # Do not include request headers or key in errors.
            if e.code in (401, 403):
                raise AIError("Ollama Cloud authentication failed. Check your API key and account access.")
            raise AIError(f"Ollama Cloud request failed (HTTP {e.code}).")
        except (urllib.error.URLError, OSError) as e:
            raise AIError(f"Ollama Cloud unreachable or timed out ({e}).")
        except json.JSONDecodeError:
            raise AIError("Ollama Cloud returned non-JSON.")
    def list_models(self):
        return [m["id"] for m in self._request("/models").get("data", [])]
    def chat_json(self, prompt, model):
        out = self._request("/chat/completions", {"model": model,
            "temperature": 0.2, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}]})
        try:
            return out["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise AIError("Unexpected reply shape from Ollama Cloud.")

PROVIDERS = {"ollama": OllamaProvider, "ollama_cloud": OllamaCloudProvider}

def get_provider(cfg):
    try:
        return PROVIDERS[cfg.provider](cfg)
    except KeyError:
        raise AIError(f"Unknown AI provider '{cfg.provider}'. Available: {', '.join(PROVIDERS)}.")
