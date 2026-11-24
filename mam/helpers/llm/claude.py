"""The Claude API provider (default). Key from `.env` (`ANTHROPIC_API_KEY`)."""

from __future__ import annotations

import logging

from mam.config import Settings
from mam.helpers.llm.base import LLMError, LLMReply

log = logging.getLogger(__name__)


class ClaudeProvider:
    name = "claude"

    def __init__(self, settings: Settings, client=None) -> None:
        self.model = settings.llm_model
        self.effort = settings.llm_effort
        self._client = client
        self._settings = settings

    @property
    def client(self):
        if self._client is None:
            import anthropic

            key = self._settings.anthropic_api_key or None  # None: the SDK's own credentials
            self._client = anthropic.Anthropic(api_key=key, timeout=self._settings.llm_timeout_s)
        return self._client

    def complete(self, system: str, user: str, *, max_tokens: int = 4000) -> LLMReply:
        import anthropic

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                output_config={"effort": self.effort},
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.AuthenticationError as exc:
            raise LLMError(
                "The Claude API key is missing or wrong. Set ANTHROPIC_API_KEY in .env"
            ) from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("The Claude API is busy (rate limit). Retry the file later") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(
                "The Claude API cannot be reached. Check the internet connection"
            ) from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(
                f"The Claude API refused the request ({exc.status_code}): {exc.message}"
            ) from exc
        if response.stop_reason == "refusal":
            raise LLMError("The model declined to write about this recording")
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise LLMError("The model returned no text")
        if response.stop_reason == "max_tokens":
            raise LLMError("The model's answer was cut off (max_tokens)")
        usage = response.usage
        return LLMReply(
            text,
            self.name,
            self.model,
            getattr(usage, "input_tokens", None),
            getattr(usage, "output_tokens", None),
        )
