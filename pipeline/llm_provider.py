#!/usr/bin/env python3
"""
Provider-agnostic LLM call dispatch for narration generation.

Phase 1, phase1-dialogue, the title rewrite, and Phase 2 are all plain
system+user text-in, JSON-text-out calls with no tool use, vision, or
streaming. That makes them a clean swap between backends: this module is the
single place that knows how to place that call against OpenAI or Claude, so
the phase modules stay backend-agnostic and only pass a `provider` string.
"""

from __future__ import annotations

import os
from typing import Any

from pipeline_logging import log_api_call_with_bodies

DEFAULT_MODEL_BY_PROVIDER: dict[str, str] = {
    "openai": "gpt-4o",
    "claude": "claude-sonnet-5",
}

_openai_client: Any = None
_anthropic_client: Any = None


def _get_openai_client() -> Any:
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI

        _openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _openai_client


def _get_anthropic_client() -> Any:
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic

        _anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _anthropic_client


def call_llm(
    provider: str,
    system_prompt: str,
    user_prompt: str,
    model: str,
    max_tokens: int,
    *,
    purpose: str | None = None,
) -> str:
    """Run one system+user turn against `provider`; return the raw text reply.

    Mirrors the OpenAI chat.completions.create behavior this pipeline was built
    around (single turn, no tools) so the existing JSON-parsing retry loops in
    Phase 1 / Phase 2 work unchanged regardless of which provider answered.
    """
    provider = (provider or "openai").strip().lower()
    extra: dict[str, Any] = {"message_count": 2, "max_tokens": max_tokens}
    if purpose:
        extra["purpose"] = purpose

    if provider in ("claude", "anthropic"):
        client = _get_anthropic_client()
        # Claude 5-gen models run adaptive thinking by default and reject an
        # explicit temperature/top_p — leave sampling on the model's default.
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = "".join(block.text for block in response.content if block.type == "text")
        log_api_call_with_bodies(
            "claude",
            "messages.create",
            request_body=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_body=raw,
            model=model,
            extra=extra,
        )
        return raw

    if provider != "openai":
        raise ValueError(f"Unknown LLM provider {provider!r}; expected 'openai' or 'claude'")

    client = _get_openai_client()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.7,
        max_tokens=max_tokens,
    )
    raw = response.choices[0].message.content or ""
    log_api_call_with_bodies(
        "openai",
        "chat.completions.create",
        request_body=messages,
        response_body=raw,
        model=model,
        extra=extra,
    )
    return raw
