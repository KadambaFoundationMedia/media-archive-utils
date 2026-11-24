"""Pluggable LLM provider with a cache, so a re-run is never billed twice."""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from mam.config import Settings
from mam.helpers.llm.base import LLMError, LLMProvider, LLMReply
from mam.helpers.local_db import models as m


def get_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "claude":
        from mam.helpers.llm.claude import ClaudeProvider

        return ClaudeProvider(settings)
    raise LLMError(f"Unknown LLM provider '{settings.llm_provider}'. Only 'claude' is built so far")


def cache_key(provider: str, model: str, prompt_version: str, system: str, user: str) -> str:
    digest = hashlib.sha256()
    for part in (provider, model, prompt_version, system, user):
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def cached_reply(
    session: Session, provider: LLMProvider, prompt_version: str, system: str, user: str
) -> LLMReply | None:
    row = session.get(
        m.LlmCache, cache_key(provider.name, provider.model, prompt_version, system, user)
    )
    if row is None:
        return None
    return LLMReply(
        row.response["text"], row.provider, row.model, row.input_tokens, row.output_tokens
    )


def store_reply(
    session: Session, prompt_version: str, system: str, user: str, reply: LLMReply
) -> None:
    key = cache_key(reply.provider, reply.model, prompt_version, system, user)
    if session.get(m.LlmCache, key) is None:
        session.add(
            m.LlmCache(
                key=key,
                provider=reply.provider,
                model=reply.model,
                prompt_version=prompt_version,
                response={"text": reply.text},
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
            )
        )


__all__ = ["LLMError", "LLMProvider", "LLMReply", "get_provider", "cached_reply", "store_reply"]
