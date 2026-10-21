"""LLM connectivity check: which provider/model/key are really in effect, and does a
real request (with our tools) succeed?  The key itself is never printed.

    python -m backend.agent.llm_check
"""
from __future__ import annotations

import os
import time
from typing import Any

from .llm import (PROVIDERS, LLMError, OpenAICompatibleLLM, load_env_file, make_llm,
                  read_env_file)
from .state import ConversationState
from .tools import TOOL_SPECS

LLM_VARS = ("LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL")


def mask(key: str | None) -> str:
    if not key:
        return "(none)"
    return f"…{key[-4:]} (length {len(key)})"


def effective_config() -> dict[str, Any]:
    """Values in effect + where each came from (shell variables win over .env)."""
    file_values = read_env_file()
    shell = {v: os.environ[v] for v in LLM_VARS if v in os.environ and
             os.environ[v] != file_values.get(v)}
    load_env_file()
    provider = (os.environ.get("LLM_PROVIDER") or "rules").lower()
    key_var = PROVIDERS.get(provider, (None, None))[1]
    key = os.environ.get("LLM_API_KEY") or (os.environ.get(key_var) if key_var else None)
    return {
        "provider": provider,
        "model": os.environ.get("LLM_MODEL"),
        "base_url": os.environ.get("LLM_BASE_URL") or PROVIDERS.get(provider, (None,))[0],
        "api_key": mask(key),
        "overridden_by_shell": sorted(shell),  # these ignore .env!
    }


def probe(config) -> dict[str, Any]:
    """One real request, same shape as the agent's (system prompt + tools)."""
    try:
        llm = make_llm(config)
    except LLMError as e:
        return {"ok": False, "stage": "config", "error": str(e)}
    if not isinstance(llm, OpenAICompatibleLLM):
        return {"ok": False, "stage": "config",
                "error": f"LLM_PROVIDER selects {type(llm).__name__} - no language model"}
    t0 = time.monotonic()
    try:
        step = llm.step(system="ענה במילה אחת.", tools=TOOL_SPECS, state=ConversationState(),
                        messages=[{"role": "user", "content": "שכר בסיס 14,200"}])
    except LLMError as e:
        return {"ok": False, "stage": "request", "status": e.status, "error": str(e)[:600],
                "seconds": round(time.monotonic() - t0, 1)}
    return {"ok": True, "seconds": round(time.monotonic() - t0, 1),
            "tool_calls": [c.name for c in step.tool_calls], "text": step.text[:200]}


def main() -> None:
    from ..data_loader.config_loader import get_config
    cfg = effective_config()
    for k, v in cfg.items():
        print(f"{k:20} {v}")
    result = probe(get_config())
    print("-" * 60)
    for k, v in result.items():
        print(f"{k:20} {v}")


if __name__ == "__main__":
    main()
