"""Checksums used in Serbian business identifiers."""

from __future__ import annotations

import re


def pib_check_digit(first8: str) -> int:
    """ISO 7064 MOD 11,10 check digit used by the Serbian tax ID (PIB)."""
    p = 10
    for ch in first8:
        s = (int(ch) + p) % 10
        if s == 0:
            s = 10
        p = (2 * s) % 11
    return (11 - p) % 10


def is_valid_pib(pib: str) -> bool:
    pib = (pib or "").strip().upper().removeprefix("RS")
    if not re.fullmatch(r"\d{9}", pib):
        return False
    return pib_check_digit(pib[:8]) == int(pib[8])


def bank_account_check(digits16: str) -> str:
    """Control number (ISO 7064 MOD 97-10) for an 18-digit Serbian current account."""
    return f"{98 - (int(digits16) * 100) % 97:02d}"


def normalize_bank_account(acc: str) -> str:
    """'160-5100-1' style accounts are zero-padded to 3-13-2 digits."""
    parts = (acc or "").replace(" ", "").split("-")
    if len(parts) == 3:
        bank, mid, ctl = parts
        return f"{bank.zfill(3)}-{mid.zfill(13)}-{ctl.zfill(2)}"
    digits = re.sub(r"\D", "", acc or "")
    if len(digits) == 18:
        return f"{digits[:3]}-{digits[3:16]}-{digits[16:]}"
    return acc or ""


def is_valid_bank_account(acc: str) -> bool:
    norm = normalize_bank_account(acc)
    digits = norm.replace("-", "")
    if not re.fullmatch(r"\d{18}", digits):
        return False
    return bank_account_check(digits[:16]) == digits[16:]
