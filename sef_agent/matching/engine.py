"""Deterministic controls: duplicates, supplier checks and 3-way match
(invoice vs purchase order vs goods receipt).

These are plain rules on purpose. Money decisions must be explainable and
repeatable, so the LLM is not used to decide whether an invoice is correct;
it is used for booking classification of free-text lines and for Q&A.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Finding, FindingType, GoodsReceipt, Invoice, InvoiceStatus, Item, PurchaseOrder, Severity,
)
from ..sef.ubl import q2
from ..sef.validators import is_valid_pib, normalize_bank_account

# Decision D4 (see DECISIONS.md): what blocks an invoice and what is only a warning.
SEVERITY: dict[FindingType, Severity] = {
    FindingType.DUPLICATE: Severity.BLOCK,
    FindingType.POSSIBLE_DUPLICATE: Severity.BLOCK,
    FindingType.UNKNOWN_SUPPLIER: Severity.BLOCK,
    FindingType.INVALID_PIB: Severity.BLOCK,
    FindingType.BANK_ACCOUNT_CHANGED: Severity.BLOCK,
    FindingType.NO_PO: Severity.BLOCK,
    FindingType.PO_SUPPLIER_MISMATCH: Severity.BLOCK,
    FindingType.PRICE_MISMATCH: Severity.BLOCK,
    FindingType.PRICE_WITHIN_TOLERANCE: Severity.WARN,
    FindingType.QTY_NOT_RECEIVED: Severity.BLOCK,
    FindingType.ITEM_NOT_ON_PO: Severity.BLOCK,
    FindingType.VAT_MISMATCH: Severity.BLOCK,
    FindingType.TOTAL_MISMATCH: Severity.BLOCK,
}
PO_REQUIRED_CATEGORIES = {"goods", "mixed"}


@dataclass
class _F:
    type: FindingType
    message: str
    line_no: int | None = None
    details: dict | None = None


def norm_number(n: str) -> str:
    """'AMB-2026-0018', 'AMB/2026/18' and 'amb 2026 0018' are the same document number."""
    parts = re.findall(r"[A-Za-z]+|\d+", n or "")
    return "-".join(p.upper() if p.isalpha() else str(int(p)) for p in parts)


def _money(x) -> str:
    return f"{Decimal(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def check_invoice(session: Session, inv: Invoice) -> list[_F]:
    out: list[_F] = []
    tol_amt = settings.amount_tolerance_rsd

    # 1. exact duplicate: same supplier + same invoice number already received
    earlier = session.execute(
        select(Invoice).where(Invoice.supplier_pib == inv.supplier_pib, Invoice.id != inv.id,
                              Invoice.sef_id < inv.sef_id)
    ).scalars().all()
    exact = [e for e in earlier if norm_number(e.number) == norm_number(inv.number)]
    if exact:
        out.append(_F(FindingType.DUPLICATE,
                      f"Faktura {inv.number} je vec primljena (SEF ID {exact[0].sef_id}).",
                      details={"original_sef_id": exact[0].sef_id}))
        return out  # nothing else matters for a re-sent document

    # 2. possible duplicate: same supplier, same total, close dates, different number
    window = timedelta(days=settings.duplicate_window_days)
    for e in earlier:
        if abs(e.total_amount - inv.total_amount) <= tol_amt and abs(e.issue_date - inv.issue_date) <= window:
            out.append(_F(FindingType.POSSIBLE_DUPLICATE,
                          f"Isti iznos ({_money(inv.total_amount)} RSD) kao faktura {e.number} od {e.issue_date:%d.%m.%Y}.",
                          details={"other_sef_id": e.sef_id, "other_number": e.number}))
            break

    # 3. supplier checks
    if not is_valid_pib(inv.supplier_pib):
        out.append(_F(FindingType.INVALID_PIB, f"PIB {inv.supplier_pib} nema ispravnu kontrolnu cifru."))
    sup = inv.supplier
    if sup is None:
        out.append(_F(FindingType.UNKNOWN_SUPPLIER,
                      f"Dobavljac {inv.supplier_name} (PIB {inv.supplier_pib}) ne postoji u maticnim podacima."))
    elif inv.bank_account and normalize_bank_account(inv.bank_account) != normalize_bank_account(sup.bank_account):
        out.append(_F(FindingType.BANK_ACCOUNT_CHANGED,
                      f"Racun za uplatu {inv.bank_account} razlikuje se od racuna iz maticnih podataka "
                      f"({sup.bank_account}). Proveriti telefonom pre placanja.",
                      details={"invoice": inv.bank_account, "master": sup.bank_account}))

    # 4. arithmetic
    line_vat = q2(sum((ln.net_amount * ln.vat_rate / 100 for ln in inv.lines), Decimal("0")))
    if abs(line_vat - inv.vat_amount) > tol_amt:
        out.append(_F(FindingType.VAT_MISMATCH,
                      f"PDV na fakturi {_money(inv.vat_amount)} a obracunato po stavkama {_money(line_vat)}.",
                      details={"header": str(inv.vat_amount), "computed": str(line_vat)}))
    if abs(inv.net_amount + inv.vat_amount - inv.total_amount) > tol_amt:
        out.append(_F(FindingType.TOTAL_MISMATCH, "Ukupan iznos nije jednak osnovici + PDV."))

    # 5. 3-way match
    if sup is None:
        return out
    if not inv.order_reference:
        if sup.category in PO_REQUIRED_CATEGORIES:
            out.append(_F(FindingType.NO_PO, "Faktura za robu nema broj narudzbenice."))
        return out
    po = session.execute(select(PurchaseOrder).where(PurchaseOrder.number == inv.order_reference)).scalar_one_or_none()
    if po is None:
        out.append(_F(FindingType.NO_PO, f"Narudzbenica {inv.order_reference} ne postoji."))
        return out
    if po.supplier_id != sup.id:
        out.append(_F(FindingType.PO_SUPPLIER_MISMATCH,
                      f"Narudzbenica {po.number} je izdata drugom dobavljacu ({po.supplier.name})."))
        return out

    po_lines = {pl.item_code: pl for pl in po.lines}
    received: dict[str, Decimal] = defaultdict(Decimal)
    for gr in session.execute(select(GoodsReceipt).where(GoodsReceipt.po_id == po.id)).scalars():
        for gl in gr.lines:
            received[gl.item_code] += gl.qty
    already: dict[str, Decimal] = defaultdict(Decimal)
    others = session.execute(
        select(Invoice).where(Invoice.order_reference == po.number, Invoice.sef_id < inv.sef_id,
                              Invoice.status != InvoiceStatus.REJECTED.value)
    ).scalars()
    for o in others:
        if any(f.type == FindingType.DUPLICATE.value for f in o.findings):
            continue
        for ln in o.lines:
            already[ln.item_code or ""] += ln.qty

    names = {it.name.lower(): it.code for it in session.execute(select(Item)).scalars()}
    tol_pct = settings.price_tolerance_pct
    for ln in inv.lines:
        code = ln.item_code or names.get(ln.description.lower())
        pl = po_lines.get(code)
        if pl is None:
            out.append(_F(FindingType.ITEM_NOT_ON_PO,
                          f"Stavka {ln.line_no} ({ln.description}) nije na narudzbenici {po.number}.", ln.line_no))
            continue
        diff_pct = (ln.unit_price - pl.unit_price) / pl.unit_price * 100
        if abs(diff_pct) > tol_pct:
            impact = q2((ln.unit_price - pl.unit_price) * ln.qty)
            out.append(_F(FindingType.PRICE_MISMATCH,
                          f"Stavka {ln.line_no}: cena {_money(ln.unit_price)} a po narudzbenici {_money(pl.unit_price)} "
                          f"({diff_pct:+.1f}%, razlika {_money(impact)} RSD).", ln.line_no,
                          {"invoice_price": str(ln.unit_price), "po_price": str(pl.unit_price),
                           "diff_pct": round(float(diff_pct), 2), "impact_rsd": str(impact)}))
        elif ln.unit_price != pl.unit_price:
            out.append(_F(FindingType.PRICE_WITHIN_TOLERANCE,
                          f"Stavka {ln.line_no}: cena odstupa {diff_pct:+.2f}% (u okviru tolerancije {tol_pct}%).",
                          ln.line_no, {"diff_pct": round(float(diff_pct), 2)}))
        available = received[code] - already[code]
        if ln.qty > available:
            out.append(_F(FindingType.QTY_NOT_RECEIVED,
                          f"Stavka {ln.line_no}: fakturisano {ln.qty.normalize()} a primljeno i nefakturisano "
                          f"{available.normalize()} {ln.unit}.", ln.line_no,
                          {"invoiced": str(ln.qty), "received": str(received[code]), "already_invoiced": str(already[code])}))
    return out


def run_matching(session: Session, inv: Invoice) -> list[Finding]:
    for f in list(inv.findings):
        session.delete(f)
    session.flush()
    results = check_invoice(session, inv)
    findings = [
        Finding(invoice_id=inv.id, type=f.type.value, severity=SEVERITY[f.type].value, line_no=f.line_no,
                message=f.message, details=f.details or {})
        for f in results
    ]
    session.add_all(findings)
    inv.findings = findings
    blocked = any(f.severity == Severity.BLOCK.value for f in findings)
    inv.status = InvoiceStatus.EXCEPTION.value if blocked else InvoiceStatus.READY.value
    session.flush()
    return findings
