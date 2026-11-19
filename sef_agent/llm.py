"""Thin wrapper around the Anthropic SDK: client, pricing and usage logging."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager

from sqlalchemy.orm import Session

from .models import LLMCall

# Decision D5: a small model classifies booking lines (high volume, narrow task),
# a strong model runs the conversational agent (multi-step tool use).
BOOKING_MODEL = os.environ.get("BOOKING_MODEL", "claude-haiku-5-5")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "claude-opus-5-5")

# USD per 1M tokens: (input, output, cache read). Anthropic list prices, Nov 2026.
PRICING = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


def llm_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("LLM_DISABLED") != "1"


_client = None


def client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()
    return _client


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0) -> float:
    pin, pout, pcache = PRICING.get(model, PRICING["claude-opus-5-5"])
    return (input_tokens * pin + output_tokens * pout + cache_read * pcache) / 1_000_000


@contextmanager
def track(session: Session | None, purpose: str, model: str, invoice_id: int | None = None):
    """Usage: with track(s, "booking", model, inv.id) as rec: resp = ...; rec(resp)"""
    start = time.perf_counter()
    holder: dict = {}

    def record(response) -> None:
        holder["usage"] = response.usage

    yield record
    usage = holder.get("usage")
    if usage is None or session is None:
        return
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    session.add(LLMCall(
        purpose=purpose, model=model, invoice_id=invoice_id, input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens, cache_read_tokens=cache_read,
        latency_ms=int((time.perf_counter() - start) * 1000),
        cost_usd=cost_usd(model, usage.input_tokens, usage.output_tokens, cache_read),
    ))
