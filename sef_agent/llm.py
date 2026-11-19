"""LLM providers (Anthropic Claude or Google Gemini): clients, model choice, pricing and usage logging.

LLM_PROVIDER=anthropic|gemini picks the provider. Left empty, the provider whose API key is set is used
(Anthropic first). Both providers are behind the same two call sites: the booking classifier and the chat agent.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager

from sqlalchemy.orm import Session

from .models import LLMCall

# Decision D5: a small model classifies booking lines (high volume, narrow task),
# a stronger model runs the conversational agent (multi-step tool use).
DEFAULT_MODELS = {
    "anthropic": {"booking": "claude-haiku-5-5", "chat": "claude-opus-5-5"},
    "gemini": {"booking": "gemini-2.5-flash-lite", "chat": "gemini-2.5-flash"},
}

# USD per 1M tokens: (input, output, cache read). Paid-tier list prices; check the providers' pricing pages.
# Gemini's free tier costs nothing, but the paid price is what a company would pay, so reports use it.
PRICING = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "gemini-2.5-flash": (0.30, 2.50, 0.03),
    "gemini-2.5-flash-lite": (0.10, 0.40, 0.01),
    "gemini-2.5-pro": (1.25, 10.00, 0.125),
}


def _gemini_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def provider() -> str | None:
    if os.environ.get("LLM_DISABLED") == "1":
        return None
    chosen = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if chosen == "anthropic":
        return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else None
    if chosen == "gemini":
        return "gemini" if _gemini_key() else None
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if _gemini_key():
        return "gemini"
    return None


def llm_available() -> bool:
    return provider() is not None


def booking_model() -> str:
    return os.environ.get("BOOKING_MODEL") or DEFAULT_MODELS[provider() or "anthropic"]["booking"]


def chat_model() -> str:
    return os.environ.get("CHAT_MODEL") or DEFAULT_MODELS[provider() or "anthropic"]["chat"]


_clients: dict = {}


def client():
    """Anthropic client."""
    if "anthropic" not in _clients:
        import anthropic

        _clients["anthropic"] = anthropic.Anthropic()
    return _clients["anthropic"]


def gemini_client():
    if "gemini" not in _clients:
        from google import genai

        _clients["gemini"] = genai.Client(api_key=_gemini_key())
    return _clients["gemini"]


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0) -> float:
    pin, pout, pcache = PRICING.get(model, (0.0, 0.0, 0.0))
    return (input_tokens * pin + output_tokens * pout + cache_read * pcache) / 1_000_000


def usage_of(response) -> tuple[int, int, int]:
    """(input, output, cache_read) tokens from either provider's response."""
    u = getattr(response, "usage", None)
    if u is not None:  # Anthropic
        return u.input_tokens, u.output_tokens, getattr(u, "cache_read_input_tokens", 0) or 0
    m = getattr(response, "usage_metadata", None)
    if m is not None:  # Gemini; thinking tokens are billed as output
        cached = m.cached_content_token_count or 0
        out = (m.candidates_token_count or 0) + (getattr(m, "thoughts_token_count", 0) or 0)
        return (m.prompt_token_count or 0) - cached, out, cached
    return 0, 0, 0


@contextmanager
def track(session: Session | None, purpose: str, model: str, invoice_id: int | None = None):
    """Usage: with track(s, "booking", model, inv.id) as rec: resp = ...; rec(resp)"""
    start = time.perf_counter()
    holder: dict = {}

    def record(response) -> None:
        holder["usage"] = usage_of(response)

    yield record
    if "usage" not in holder or session is None:
        return
    inp, out, cache = holder["usage"]
    session.add(LLMCall(
        purpose=purpose, model=model, invoice_id=invoice_id, input_tokens=inp, output_tokens=out,
        cache_read_tokens=cache, latency_ms=int((time.perf_counter() - start) * 1000),
        cost_usd=cost_usd(model, inp, out, cache),
    ))
