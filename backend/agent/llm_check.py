"""LLM connectivity check: which providers/models/keys are really in effect, and does a
real request (with our tools) succeed on each one?  Keys are never printed.

    python -m backend.agent.llm_check

Each configured slot (LLM_PROVIDER, LLM_PROVIDER_2, ...) is checked on its own, so a
backup that would fail is found before it is needed.
"""
from __future__ import annotations

import os
import time
from typing import Any

from .llm import (PROVIDERS, RULES, LLMError, configured_slots, load_env_file,
                  make_slot_client, read_env_file, slot_suffix)
from .state import ConversationState
from .tools import TOOL_SPECS

LLM_VARS = ("LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL")


def mask(key: str | None) -> str:
    if not key:
        return "(none)"
    return f"…{key[-4:]} (length {len(key)})"


def effective_config() -> list[dict[str, Any]]:
    """Per slot: values in effect + which ones a shell variable overrides (shell wins)."""
    file_values = read_env_file()
    load_env_file()
    env = dict(os.environ)
    slots = []
    for n in configured_slots(env) or [1]:
        s = slot_suffix(n)
        names = [v + s for v in LLM_VARS]
        provider = (env.get("LLM_PROVIDER" + s) or "(not set)").lower()
        key_var = PROVIDERS.get(provider, (None, None))[1]
        key = env.get("LLM_API_KEY" + s) or (env.get(key_var) if key_var else None)
        slots.append({
            "slot": n,
            "provider": provider,
            "model": env.get("LLM_MODEL" + s),
            "base_url": env.get("LLM_BASE_URL" + s) or PROVIDERS.get(provider, (None,))[0],
            "api_key": mask(key),
            "overridden_by_shell": sorted(v for v in names if v in os.environ
                                          and os.environ[v] != file_values.get(v, os.environ[v])),
        })
    return slots


def probe_client(client: Any) -> dict[str, Any]:
    """One real request, same shape as the agent's (system prompt + tools)."""
    t0 = time.monotonic()
    try:
        step = client.step(system="ענה במילה אחת.", tools=TOOL_SPECS, state=ConversationState(),
                           messages=[{"role": "user", "content": "שכר בסיס 14,200"}])
    except LLMError as e:
        return {"ok": False, "stage": "request", "status": e.status,
                "message": e.user_message, "error": str(e)[:600],
                "seconds": round(time.monotonic() - t0, 1)}
    return {"ok": True, "seconds": round(time.monotonic() - t0, 1),
            "tool_calls": [c.name for c in step.tool_calls], "text": step.text[:200]}


def probe_all() -> list[dict[str, Any]]:
    load_env_file()
    env = dict(os.environ)
    if (env.get("LLM_PROVIDER") or "").strip().lower() == RULES:
        return [{"slot": 1, "ok": False, "stage": "config",
                 "error": "LLM_PROVIDER=rules - no language model is configured"}]
    results = []
    for n in configured_slots(env) or [1]:
        try:
            client = make_slot_client(env, n)
        except LLMError as e:
            results.append({"slot": n, "ok": False, "stage": "config", "error": str(e)})
            continue
        results.append({"slot": n, "provider": client.label, **probe_client(client)})
    return results


def main() -> None:
    for cfg in effective_config():
        print(f"--- slot {cfg.pop('slot')} " + "-" * 44)
        for k, v in cfg.items():
            print(f"{k:20} {v}")
    print("=" * 60)
    for r in probe_all():
        status = "OK " if r["ok"] else "FAIL"
        print(f"slot {r['slot']} {status} {r.get('provider', '')}")
        for k, v in r.items():
            if k not in ("slot", "ok", "provider"):
                print(f"    {k:16} {v}")


if __name__ == "__main__":
    main()
