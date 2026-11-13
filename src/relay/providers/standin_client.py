"""Stand-in streaming HTTP/SSE client sharing exact semantics with GroqProvider."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI

from relay.providers.base import (
    ChatMessage,
    ModelProvider,
    StreamChunk,
    stream_openai_sse,
)


class StandInProvider(ModelProvider):
    """Client talking to the local Stand-in HTTP/SSE service (via TCP URL or ASGI transport)."""

    name = "stand-in"
    source_label = "stand-in"

    def __init__(
        self,
        *,
        base_url: str = "http://standin.local/openai/v1",
        app: FastAPI | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._app = app
        self._external_client = client

    def _make_client(self) -> tuple[httpx.AsyncClient, bool]:
        if self._external_client is not None:
            return self._external_client, False
        if self._app is not None:
            transport = httpx.ASGITransport(app=self._app)
            return httpx.AsyncClient(transport=transport, base_url="http://standin.local"), True
        return httpx.AsyncClient(), True

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str = "standin-llama-3.1-8b",
        temperature: float = 0.0,
        max_tokens: int = 512,
        timeout_s: float = 15.0,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[StreamChunk]:
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError("Cancelled before Stand-in request started")

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        client, own_client = self._make_client()
        try:
            async for chunk in stream_openai_sse(
                client,
                url=url,
                headers={"Content-Type": "application/json"},
                payload=payload,
                timeout_s=timeout_s,
                provider_label="Stand-in",
                cancel_event=cancel_event,
            ):
                yield chunk
        finally:
            if own_client:
                await client.aclose()
