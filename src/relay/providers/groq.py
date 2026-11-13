"""Real Groq free-tier async streaming provider with full header and error inspection."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx

from relay.config import DEFAULT_GROQ_MODEL, get_groq_api_key
from relay.providers.base import (
    ChatMessage,
    ModelProvider,
    ProviderError,
    StreamChunk,
    stream_openai_sse,
)


class GroqProvider(ModelProvider):
    """Streaming client for Groq's OpenAI-compatible chat completions API."""

    name = "groq"
    source_label = "real"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.groq.com/openai/v1",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else get_groq_api_key()
        self.base_url = base_url.rstrip("/")
        self._external_client = client

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str = DEFAULT_GROQ_MODEL,
        temperature: float = 0.0,
        max_tokens: int = 512,
        timeout_s: float = 15.0,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[StreamChunk]:
        if not self._api_key:
            raise ProviderError("GROQ_API_KEY is not configured", status_code=401)
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError("Cancelled before Groq request started")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        own_client = self._external_client is None
        client = self._external_client or httpx.AsyncClient()
        try:
            async for chunk in stream_openai_sse(
                client,
                url=url,
                headers=headers,
                payload=payload,
                timeout_s=timeout_s,
                provider_label="Groq",
                cancel_event=cancel_event,
            ):
                yield chunk
        finally:
            if own_client:
                await client.aclose()
