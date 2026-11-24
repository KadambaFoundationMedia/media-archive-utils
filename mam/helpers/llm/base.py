"""The LLM provider interface (SPEC.md section 1).

A provider turns a system prompt and a user prompt into text. Tools ask for JSON in their
prompt and validate it themselves, so every provider (the Claude API now, a local model later)
works the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mam.errors import MamError


class LLMError(MamError):
    """The model could not be reached, declined, or answered with nothing usable."""


@dataclass
class LLMReply:
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str, *, max_tokens: int = 4000) -> LLMReply: ...
