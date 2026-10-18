"""Provider-neutral LLM layer for the agent loop.

The transcript and tool definitions use the OpenAI Chat Completions format
(messages with role user/assistant/tool, `tool_calls`, function tools), which
OpenAI, Gemini, Anthropic, Ollama, vLLM, LM Studio and LiteLLM all accept.

Selection is by environment variables (or a project `.env` file):

    LLM_PROVIDER   rules (default) | openai | anthropic | gemini | ollama |
                   openai_compatible | litellm
    LLM_MODEL      model name, e.g. gpt-4.1, claude-opus-5-5, gemini-2.5-pro, llama3.1
    LLM_API_KEY    API key (falls back to the provider's usual variable)
    LLM_BASE_URL   override endpoint (required for openai_compatible)

`rules` is the deterministic RuleBasedLLM - no network, no key, no cost; it is
what the automated tests use.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from ..data_loader import paths
from .state import ConversationState

REP_PREFIX = "[תשובת נציג/ה]"  # how representative answers appear in the LLM transcript

# provider -> (default base URL of its OpenAI-compatible endpoint, provider key variable)
PROVIDERS: dict[str, tuple[Optional[str], Optional[str]]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "anthropic": ("https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
    "openai_compatible": (None, None),
}
RULES = "rules"
LITELLM = "litellm"


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMStep:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_message: dict[str, Any] = field(default_factory=dict)


class LLMClient(Protocol):
    def step(self, *, system: str, messages: list[dict[str, Any]],
             tools: list[dict[str, Any]], state: ConversationState) -> LLMStep: ...


def assistant_message(text: str, calls: list[ToolCall]) -> dict[str, Any]:
    """Assistant turn in Chat Completions format."""
    msg: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        msg["tool_calls"] = [
            {"id": c.id, "type": "function",
             "function": {"name": c.name, "arguments": json.dumps(c.input, ensure_ascii=False)}}
            for c in calls
        ]
    return msg


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """{name, description, input_schema} -> Chat Completions function tools."""
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["input_schema"]}} for t in tools]


def parse_completion(data: dict[str, Any]) -> LLMStep:
    """Chat Completions response -> LLMStep.

    The assistant message sent back on the next request keeps each tool call exactly
    as the provider returned it (plus a generated id if it had none). Providers attach
    opaque data there that they require back - e.g. Gemini 3 puts a mandatory
    `extra_content.google.thought_signature` on function calls."""
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"unexpected completion payload: {str(data)[:300]}") from e
    calls, raw_calls = [], []
    for tc in msg.get("tool_calls") or []:
        tc = {**tc, "id": tc.get("id") or f"call_{uuid.uuid4().hex[:12]}"}
        fn = tc.get("function", {})
        args = fn.get("arguments") or "{}"
        try:
            parsed = json.loads(args) if isinstance(args, str) else dict(args)
        except json.JSONDecodeError:
            parsed = {"_invalid_arguments": args}
        calls.append(ToolCall(tc["id"], fn.get("name", ""), parsed))
        raw_calls.append(tc)
    text = msg.get("content") or ""
    echo: dict[str, Any] = {"role": "assistant", "content": text or None}
    if raw_calls:
        echo["tool_calls"] = raw_calls
    if msg.get("extra_content"):
        echo["extra_content"] = msg["extra_content"]
    return LLMStep(text, calls, echo)


class Secret(str):
    """A string that never shows its value in repr() - so API keys stay out of
    tracebacks, pytest failure output and logs, while still working as a header."""

    def __repr__(self) -> str:
        return "'***'"


Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


def http_post_json(url: str, headers: dict[str, str], body: dict[str, Any],
                   timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise LLMError(f"LLM HTTP {e.code} from {url}: {detail}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"LLM endpoint unreachable ({url}): {e.reason}") from e


class OpenAICompatibleLLM:
    """Any endpoint that speaks Chat Completions with tool calling."""

    def __init__(self, model: str, base_url: str, api_key: Optional[str] = None,
                 temperature: float = 0.0, max_tokens: int = 2048, timeout: float = 60.0,
                 transport: Transport = http_post_json):
        self.model, self.base_url = model, base_url.rstrip("/")
        self.api_key = Secret(api_key) if api_key else None
        self.temperature, self.max_tokens, self.timeout = temperature, max_tokens, timeout
        self.transport = transport

    def request_body(self, system: str, messages: list[dict[str, Any]],
                     tools: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "tools": to_openai_tools(tools),
            "tool_choice": "auto",
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

    def step(self, *, system, messages, tools, state) -> LLMStep:
        headers = {"Authorization": Secret(f"Bearer {self.api_key}")} if self.api_key else {}
        data = self.transport(f"{self.base_url}/chat/completions", headers,
                              self.request_body(system, messages, tools), self.timeout)
        return parse_completion(data)


class LiteLLMClient:
    """Routes through LiteLLM (optional dependency) - model names like 'gemini/gemini-2.5-pro'."""

    def __init__(self, model: str, api_key: Optional[str] = None,
                 base_url: Optional[str] = None, temperature: float = 0.0):
        import litellm  # optional dependency
        self._litellm = litellm
        self.model, self.base_url, self.temperature = model, base_url, temperature
        self.api_key = Secret(api_key) if api_key else None

    def step(self, *, system, messages, tools, state) -> LLMStep:
        resp = self._litellm.completion(
            model=self.model, api_key=self.api_key, base_url=self.base_url,
            temperature=self.temperature, tool_choice="auto", tools=to_openai_tools(tools),
            messages=[{"role": "system", "content": system}, *messages],
        )
        return parse_completion(resp.model_dump() if hasattr(resp, "model_dump") else resp)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def load_env_file(path: Path = paths.PROJECT_ROOT / ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines; never overrides real environment variables."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def make_llm(config, env: Optional[dict[str, str]] = None) -> LLMClient:
    """Build the LLM client from LLM_PROVIDER / LLM_MODEL / LLM_API_KEY / LLM_BASE_URL."""
    if env is None:
        load_env_file()
        env = dict(os.environ)
    provider = (env.get("LLM_PROVIDER") or RULES).strip().lower()
    if provider == RULES:
        from .rule_based import RuleBasedLLM
        return RuleBasedLLM(config)

    model = env.get("LLM_MODEL")
    if not model:
        raise LLMError(f"LLM_MODEL must be set for LLM_PROVIDER={provider}")
    base_url = env.get("LLM_BASE_URL") or None
    if provider == LITELLM:
        return LiteLLMClient(model, env.get("LLM_API_KEY"), base_url)
    if provider not in PROVIDERS:
        raise LLMError(f"unknown LLM_PROVIDER {provider!r}; "
                       f"use one of: {', '.join([RULES, *PROVIDERS, LITELLM])}")
    default_url, key_var = PROVIDERS[provider]
    base_url = base_url or default_url
    if not base_url:
        raise LLMError(f"LLM_BASE_URL must be set for LLM_PROVIDER={provider}")
    api_key = env.get("LLM_API_KEY") or (env.get(key_var) if key_var else None)
    if key_var and not api_key:
        raise LLMError(f"LLM_API_KEY (or {key_var}) must be set for LLM_PROVIDER={provider}")
    return OpenAICompatibleLLM(model, base_url, api_key)
