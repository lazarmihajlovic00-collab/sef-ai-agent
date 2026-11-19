"""Account (konto) classification for invoice lines.

Lines with a known item code take the account from the item master (deterministic).
Free-text lines (services, utilities, unknown suppliers) need classification:
  - RuleClassifier: keyword baseline, no API cost
  - LLMClassifier: Claude with structured output, returns account + confidence + reason
The evaluation compares both on the same labelled lines.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from ..domain.chart_of_accounts import ACCOUNT_GUIDE, EXPENSE_ACCOUNTS
from .. import llm


@dataclass
class LineInput:
    description: str
    supplier_name: str
    supplier_category: str | None
    amount: str


@dataclass
class Classification:
    account: str
    confidence: float
    reason: str
    source: str


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c)).replace("đ", "dj")


KEYWORDS: list[tuple[str, str]] = [
    (r"gorivo|dizel|benzin|struj|elektricn|energij|gas\b|grejanj", "5130"),
    (r"kurir|prevoz|transport|dostav|spedic", "5310"),
    (r"internet|telefon|mobiln|pretplat|posta|ptt", "5311"),
    (r"zakup|zakupnin|najam", "5330"),
    (r"reklam|marketing|letak|letk|drustven|oglas|kampanj", "5350"),
    (r"servis|popravk|odrzavanj|ugradnj", "5320"),
    (r"licenc|softver|knjigovod|racunovod|konsult|zavrsn|revizij|obezbedj", "5500"),
    (r"toner|papir|kancelarij|sapun|ubrus|higijen", "5120"),
]


class RuleClassifier:
    name = "rules"

    def classify(self, line: LineInput) -> Classification:
        text = _norm(line.description)
        for pattern, account in KEYWORDS:
            if re.search(pattern, text):
                return Classification(account, 0.7, f"kljucna rec: /{pattern.split('|')[0]}/", "rules")
        return Classification("5500", 0.3, "nema poklapanja, podrazumevano 5500", "rules")


class _LLMAnswer(BaseModel):
    account: Literal[tuple(EXPENSE_ACCOUNTS)]  # type: ignore[valid-type]
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(description="Jedna kratka recenica na srpskom (latinica).")


SYSTEM = f"""Ti si iskusan knjigovodja u srpskom trgovinskom preduzecu.
Za stavku ulazne fakture izaberi analiticki konto troska ili zaliha iz liste ispod.
Vrati konto, procenu sigurnosti od 0 do 1 i kratko obrazlozenje.
Ako opis nije dovoljan za siguran izbor, spusti sigurnost ispod 0.6.

Kontni okvir (izvod):
{ACCOUNT_GUIDE}"""


class LLMClassifier:
    name = "llm"

    def __init__(self, model: str | None = None, session=None):
        self.model = model or llm.BOOKING_MODEL
        self.session = session

    def classify(self, line: LineInput, invoice_id: int | None = None) -> Classification:
        prompt = (
            f"Dobavljac: {line.supplier_name} (kategorija: {line.supplier_category or 'nepoznat dobavljac'})\n"
            f"Opis stavke: {line.description}\nIznos bez PDV-a: {line.amount} RSD"
        )
        with llm.track(self.session, "booking", self.model, invoice_id) as rec:
            resp = llm.client().messages.parse(
                model=self.model,
                max_tokens=2000,
                system=SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                output_format=_LLMAnswer,
            )
            rec(resp)
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            return Classification("5500", 0.0, "model nije vratio odgovor", "llm")
        a = resp.parsed_output
        return Classification(a.account, float(a.confidence), a.reason, "llm")
