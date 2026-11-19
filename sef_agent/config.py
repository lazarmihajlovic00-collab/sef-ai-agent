"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///./data/sef_agent.db"))

    # SEF (Sistem e-faktura). "mock" reads generated UBL files from MOCK_SEF_DIR;
    # "http" talks to the real SEF public API (demo or production).
    sef_mode: str = field(default_factory=lambda: _env("SEF_MODE", "mock"))
    sef_base_url: str = field(default_factory=lambda: _env("SEF_BASE_URL", "https://demoefaktura.mfin.gov.rs"))
    sef_api_key: str = field(default_factory=lambda: _env("SEF_API_KEY", ""))
    mock_sef_dir: Path = field(default_factory=lambda: Path(_env("MOCK_SEF_DIR", "./data/sef_inbox")))

    # Legacy flag; provider selection lives in llm.py (Anthropic or Gemini key).
    # and the chat endpoint answers that the LLM is disabled.
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "claude-opus-5-5"))
    llm_effort: str = field(default_factory=lambda: _env("LLM_EFFORT", "low"))
    llm_enabled: bool = field(
        default_factory=lambda: bool(os.environ.get("ANTHROPIC_API_KEY")) and _env("LLM_DISABLED", "") != "1"
    )

    # Matching tolerances
    price_tolerance_pct: Decimal = field(default_factory=lambda: Decimal(_env("PRICE_TOLERANCE_PCT", "1.0")))
    amount_tolerance_rsd: Decimal = field(default_factory=lambda: Decimal(_env("AMOUNT_TOLERANCE_RSD", "1.00")))
    duplicate_window_days: int = field(default_factory=lambda: int(_env("DUPLICATE_WINDOW_DAYS", "45")))

    # Buyer (our company) identity used by the generator and SEF mock
    buyer_name: str = field(default_factory=lambda: _env("BUYER_NAME", "Demo Trgovina d.o.o."))
    buyer_pib: str = field(default_factory=lambda: _env("BUYER_PIB", "109876543"))


settings = Settings()
