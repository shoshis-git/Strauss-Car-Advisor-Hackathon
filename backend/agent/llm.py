"""Provider-neutral LLM layer for the agent loop.

The transcript and tool definitions use the OpenAI Chat Completions format
(messages with role user/assistant/tool, `tool_calls`, function tools), which
OpenAI, Gemini, Anthropic, Ollama, vLLM, LM Studio and LiteLLM all accept.

Selection is by environment variables (or a project `.env` file):

    LLM_PROVIDER   rules | openai | anthropic | gemini | groq | openrouter | ollama |
                   openai_compatible | litellm
    LLM_MODEL      model name, e.g. gpt-4.1, claude-opus-5-5, gemini-2.5-pro, llama3.1
    LLM_API_KEY    API key (falls back to the provider's usual variable)
    LLM_BASE_URL   override endpoint (required for openai_compatible)

Backup providers / keys: the same four variables with a suffix _2, _3 ... _5. When one
fails (daily quota, credit, outage) the next one answers - see FailoverLLM.

`rules` is the deterministic RuleBasedLLM - no network, no key, no cost; it is
what the automated tests use.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from ..data_loader import paths
from .state import ConversationState

REP_PREFIX = "[תשובת נציג/ה]"  # how representative answers appear in the LLM transcript

log = logging.getLogger("strauss.llm")

# provider -> (default base URL of its OpenAI-compatible endpoint, provider key variable)
PROVIDERS: dict[str, tuple[Optional[str], Optional[str]]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "anthropic": ("https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
    "openai_compatible": (None, None),
}
RULES = "rules"
LITELLM = "litellm"


TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """status: HTTP status from the provider; None = unreachable / malformed reply.
    detail: the provider's own short explanation (safe to log - long tokens masked)."""

    def __init__(self, message: str, status: Optional[int] = None, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.detail = detail

    @property
    def transient(self) -> bool:
        """Quota, overload, outage or network - worth backing off rather than retrying now."""
        return self.status is None or self.status in TRANSIENT_STATUSES

    @property
    def user_message(self) -> str:
        """What actually went wrong, in Hebrew, without provider internals or secrets."""
        # classify by HTTP status; Gemini's 429 text also says "check your plan and billing"
        text = str(self).lower()
        if self.status == 402 or (self.status is None and "prepayment credits" in text):
            return ("נגמרה יתרת הקרדיט בחשבון ספק מודל השפה (HTTP 402). "
                    "יש להטעין קרדיט בפרויקט של המפתח (ב-Gemini: AI Studio ← Billing).")
        if self.status == 429:
            return ("חריגה ממכסת הבקשות של ספק מודל השפה (HTTP 429). "
                    "יש להמתין לאיפוס המכסה, להגדיל אותה או להחליף מודל.")
        if self.status in (401, 403):
            return f"מפתח ה-API נדחה על ידי ספק מודל השפה (HTTP {self.status}) - שגוי, בוטל או ללא הרשאה."
        if self.status == 404:
            return "המודל שהוגדר ב-LLM_MODEL לא נמצא אצל הספק (HTTP 404)."
        if self.status == 400:
            return "ספק מודל השפה דחה את הבקשה (HTTP 400)."
        if self.status is None:
            return "לא ניתן להתחבר לספק מודל השפה (רשת / כתובת / הגדרות)."
        return f"תקלה אצל ספק מודל השפה (HTTP {self.status}). נסו שוב בעוד רגע."


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


_LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-]{24,}")  # keys / tokens a provider might echo


def provider_detail(body: str) -> str:
    """The provider's own error message (JSON error.message if present), short and with
    anything key-like masked - so it can go to the server log."""
    text = body
    try:
        err = json.loads(body)
        err = err[0] if isinstance(err, list) and err else err
        msg = err.get("error", {}) if isinstance(err, dict) else {}
        text = (msg.get("message") if isinstance(msg, dict) else msg) or body
    except (ValueError, AttributeError):
        pass
    return _LONG_TOKEN.sub("…", " ".join(str(text).split()))[:200]


def http_post_json(url: str, headers: dict[str, str], body: dict[str, Any],
                   timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")[:2000]
        raise LLMError(f"LLM HTTP {e.code} from {url}: {raw[:500]}", status=e.code,
                       detail=provider_detail(raw)) from e
    except urllib.error.URLError as e:
        raise LLMError(f"LLM endpoint unreachable ({url}): {e.reason}",
                       detail=str(e.reason)[:200]) from e
    except TimeoutError as e:
        raise LLMError(f"LLM endpoint timed out ({url})", status=408, detail="timeout") from e
    except (UnicodeError, ValueError, OSError, http.client.HTTPException) as e:
        # e.g. a non-Latin character in the API key header, or a reply that is not JSON:
        # a provider failure like any other (the next provider is tried, no 500). The
        # exception text is not kept - it could quote the header value.
        raise LLMError(f"LLM request to {url} failed: {type(e).__name__}",
                       detail=type(e).__name__) from None


# Documented by Google for function calls that Gemini did not produce itself (e.g. a turn
# that started on another provider): it skips the thought-signature check for that call.
GEMINI_FOREIGN_SIGNATURE = "skip_thought_signature_validator"


ORIGIN_KEY = "_origin"  # which client produced an assistant message (never sent out)


def _adapt_history(messages: list[dict[str, Any]], label: str,
                   provider: str) -> list[dict[str, Any]]:
    """Make a transcript that another provider started (failover mid-turn) acceptable.

    Own messages go back exactly as received (Gemini requires its thought signatures in
    `extra_content`). Foreign tool calls lose provider-specific extras; for Gemini they get
    the placeholder signature instead. The origin tag itself is always removed."""
    out = []
    for m in messages:
        if ORIGIN_KEY not in m:
            out.append(m)
            continue
        origin = m[ORIGIN_KEY]
        m = {k: v for k, v in m.items() if k != ORIGIN_KEY}
        if origin == label or m.get("role") != "assistant" or not m.get("tool_calls"):
            out.append(m)
            continue
        m = {k: v for k, v in m.items() if k != "extra_content"}
        calls = [{k: v for k, v in tc.items() if k != "extra_content"} for tc in m["tool_calls"]]
        if provider == "gemini":
            calls = [{**tc, "extra_content": {"google": {
                "thought_signature": GEMINI_FOREIGN_SIGNATURE}}} for tc in calls]
        out.append({**m, "tool_calls": calls})
    return out


class OpenAICompatibleLLM:
    """Any endpoint that speaks Chat Completions with tool calling."""

    def __init__(self, model: str, base_url: str, api_key: Optional[str] = None,
                 temperature: float = 0.0, max_tokens: int = 2048, timeout: float = 60.0,
                 transport: Transport = http_post_json, provider: str = "openai_compatible"):
        self.model, self.base_url = model, base_url.rstrip("/")
        self.api_key = Secret(api_key) if api_key else None
        self.temperature, self.max_tokens, self.timeout = temperature, max_tokens, timeout
        self.transport = transport
        self.provider = provider

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    def request_body(self, system: str, messages: list[dict[str, Any]],
                     tools: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         *_adapt_history(messages, self.label, self.provider)],
            "tools": to_openai_tools(tools),
            "tool_choice": "auto",
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

    def step(self, *, system, messages, tools, state) -> LLMStep:
        headers = {"Authorization": Secret(f"Bearer {self.api_key}")} if self.api_key else {}
        data = self.transport(f"{self.base_url}/chat/completions", headers,
                              self.request_body(system, messages, tools), self.timeout)
        step = parse_completion(data)
        step.assistant_message[ORIGIN_KEY] = self.label
        return step


class LiteLLMClient:
    """Routes through LiteLLM (optional dependency) - model names like 'gemini/gemini-2.5-pro'."""

    def __init__(self, model: str, api_key: Optional[str] = None,
                 base_url: Optional[str] = None, temperature: float = 0.0):
        import litellm  # optional dependency
        self._litellm = litellm
        self.model, self.base_url, self.temperature = model, base_url, temperature
        self.api_key = Secret(api_key) if api_key else None
        self.provider = LITELLM

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    def step(self, *, system, messages, tools, state) -> LLMStep:
        resp = self._litellm.completion(
            model=self.model, api_key=self.api_key, base_url=self.base_url,
            temperature=self.temperature, tool_choice="auto", tools=to_openai_tools(tools),
            messages=[{"role": "system", "content": system},
                      *_adapt_history(messages, self.label, self.provider)],
        )
        step = parse_completion(resp.model_dump() if hasattr(resp, "model_dump") else resp)
        step.assistant_message[ORIGIN_KEY] = self.label
        return step


DEFAULT_FAILOVER_COOLDOWN_S = 600.0
NO_COOLDOWN_STATUSES = {400}  # this request was refused (e.g. images) - not the provider


class FailoverLLM:
    """Several providers / keys in a fixed order (LLM_PROVIDER, LLM_PROVIDER_2, ...).

    Each request goes to the first one that is not cooling down. A provider that fails
    (quota, credit, bad key, missing model, outage) cools down for `cooldown_s` and the
    next one answers the same request; after the cooldown it is tried first again - so
    when a daily quota resets, the primary is back without touching .env. A 400 (this
    particular request refused, e.g. an image for a text-only model) moves on without a
    cooldown. If every provider fails, the error of the last one is raised."""

    def __init__(self, clients: list[Any], cooldown_s: Optional[float] = None,
                 clock: Callable[[], float] = time.monotonic):
        if not clients:
            raise ValueError("FailoverLLM needs at least one client")
        self.clients = clients
        self.cooldown_s = (float(os.environ.get("LLM_FAILOVER_COOLDOWN_S",
                                                DEFAULT_FAILOVER_COOLDOWN_S))
                           if cooldown_s is None else cooldown_s)
        self._clock = clock
        self._down_until = [0.0] * len(clients)
        self.last_used: Optional[int] = None  # index of the client that answered last

    @property
    def model(self) -> str:
        """For logs: the chain in order."""
        return " → ".join(getattr(c, "label", type(c).__name__) for c in self.clients)

    def step(self, **kw) -> LLMStep:
        now = self._clock()
        ready = [i for i in range(len(self.clients)) if now >= self._down_until[i]]
        order = ready or list(range(len(self.clients)))  # all cooling down: try them anyway
        last: Optional[LLMError] = None
        for i in order:
            client = self.clients[i]
            try:
                step = client.step(**kw)
            except LLMError as e:
                last = e
                if e.status not in NO_COOLDOWN_STATUSES:
                    self._down_until[i] = self._clock() + self.cooldown_s
                log.warning("LLM %s failed (status %s: %s)%s - trying the next provider",
                            getattr(client, "label", i), e.status, e.detail or "-",
                            "" if e.status in NO_COOLDOWN_STATUSES
                            else f", cooling down {self.cooldown_s:.0f}s")
                continue
            if self.last_used is not None and i != self.last_used:
                log.warning("LLM now answered by %s", getattr(client, "label", i))
            self.last_used = i
            return step
        assert last is not None
        raise last


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
ENV_FILE = paths.PROJECT_ROOT / ".env"


def read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """KEY=VALUE lines of a .env file (comments and blanks ignored)."""
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_env_file(path: Path = ENV_FILE) -> None:
    """Minimal .env loader; never overrides real environment variables."""
    for key, value in read_env_file(path).items():
        os.environ.setdefault(key, value)


MAX_PROVIDER_SLOTS = 5


def slot_suffix(n: int) -> str:
    """Slot 1 is the plain LLM_PROVIDER...; backups are LLM_PROVIDER_2 ... _5."""
    return "" if n == 1 else f"_{n}"


def configured_slots(env: dict[str, str]) -> list[int]:
    return [n for n in range(1, MAX_PROVIDER_SLOTS + 1)
            if (env.get(f"LLM_PROVIDER{slot_suffix(n)}") or "").strip()]


def _check_value(name: str, value: Optional[str]) -> None:
    """A value copied from an example ('<API key from OpenRouter>') instead of a real one:
    stop at startup with the variable's name, not with a crash on the first request."""
    if value and ("<" in value or ">" in value or not value.isascii() or " " in value.strip()):
        raise LLMError(f"{name} looks like placeholder text, not a real value - "
                       "replace it in .env (or delete the line)")


def make_slot_client(env: dict[str, str], n: int) -> Any:
    """One provider slot -> its client (errors name the slot's own variables)."""
    s = slot_suffix(n)
    for var in ("LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL"):
        _check_value(var + s, env.get(var + s))
    provider = (env.get(f"LLM_PROVIDER{s}") or "").strip().lower()
    if provider == RULES:
        raise LLMError(f"LLM_PROVIDER{s}=rules is not a backup model - the deterministic "
                       "fallback is LLM_FALLBACK=rules")
    model = env.get(f"LLM_MODEL{s}")
    if not model:
        raise LLMError(f"LLM_MODEL{s} must be set for LLM_PROVIDER{s}={provider}")
    base_url = env.get(f"LLM_BASE_URL{s}") or None
    if provider == LITELLM:
        return LiteLLMClient(model, env.get(f"LLM_API_KEY{s}"), base_url)
    if provider not in PROVIDERS:
        raise LLMError(f"unknown LLM_PROVIDER{s} {provider!r}; "
                       f"use one of: {', '.join([RULES, *PROVIDERS, LITELLM])}")
    default_url, key_var = PROVIDERS[provider]
    base_url = base_url or default_url
    if not base_url:
        raise LLMError(f"LLM_BASE_URL{s} must be set for LLM_PROVIDER{s}={provider}")
    api_key = env.get(f"LLM_API_KEY{s}") or (env.get(key_var) if key_var else None)
    if key_var and not env.get(f"LLM_API_KEY{s}"):
        _check_value(key_var, api_key)
    if key_var and not api_key:
        raise LLMError(f"LLM_API_KEY{s} (or {key_var}) must be set for LLM_PROVIDER{s}={provider}")
    return OpenAICompatibleLLM(model, base_url, api_key, provider=provider)


def make_llm(config, env: Optional[dict[str, str]] = None) -> LLMClient:
    """Build the LLM client from LLM_PROVIDER / LLM_MODEL / LLM_API_KEY / LLM_BASE_URL,
    plus backups LLM_PROVIDER_2 ... (same variables with the suffix) -> FailoverLLM.

    LLM_PROVIDER is mandatory: the advisor runs on a language model. 'rules' (the
    deterministic brain, no model) must be chosen explicitly - for tests/offline only."""
    if env is None:
        load_env_file()
        env = dict(os.environ)
    provider = (env.get("LLM_PROVIDER") or "").strip().lower()
    if not provider:
        raise LLMError("LLM_PROVIDER is not set - configure a language model in .env "
                       "(openai / anthropic / gemini / groq / openrouter / ollama / "
                       "openai_compatible / litellm), or LLM_PROVIDER=rules explicitly for "
                       "offline tests")
    if provider == RULES:
        from .rule_based import RuleBasedLLM
        return RuleBasedLLM(config)
    clients = [make_slot_client(env, n) for n in configured_slots(env)]
    return clients[0] if len(clients) == 1 else FailoverLLM(clients)
