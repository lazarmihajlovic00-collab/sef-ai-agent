"""Turn a matched invoice into a proposed journal entry (temeljnica)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.chart_of_accounts import ACCOUNTS, PAYABLES_ACCOUNT, VAT_ACCOUNT
from ..models import BookingProposal, Invoice, Item, PurchaseOrder
from ..sef.ubl import q2
from .classifier import Classification, LineInput, LLMClassifier, RuleClassifier


def classify_lines(session: Session, inv: Invoice, classifier) -> list[Classification]:
    items = {it.code: it for it in session.execute(select(Item)).scalars()}
    out = []
    for ln in inv.lines:
        it = items.get(ln.item_code or "")
        if it is not None:
            out.append(Classification(it.gl_account, 0.99, f"artikal {it.code} iz sifarnika", "master"))
            continue
        li = LineInput(ln.description, inv.supplier_name, inv.supplier.category if inv.supplier else None,
                       str(ln.net_amount))
        if isinstance(classifier, LLMClassifier):
            out.append(classifier.classify(li, invoice_id=inv.id))
        else:
            out.append(classifier.classify(li))
    return out


def propose_booking(session: Session, inv: Invoice, classifier=None) -> BookingProposal:
    classifier = classifier or RuleClassifier()
    cls = classify_lines(session, inv, classifier)
    cost_center = None
    if inv.order_reference:
        po = session.execute(select(PurchaseOrder).where(PurchaseOrder.number == inv.order_reference)).scalar_one_or_none()
        cost_center = po.cost_center if po else None

    debit: dict[str, Decimal] = defaultdict(Decimal)
    for ln, c in zip(inv.lines, cls):
        debit[c.account] += ln.net_amount
    vat: dict[str, Decimal] = defaultdict(Decimal)
    for ln in inv.lines:
        rate = int(ln.vat_rate)
        if rate in VAT_ACCOUNT:
            vat[VAT_ACCOUNT[rate]] += q2(ln.net_amount * ln.vat_rate / 100)

    entries = [{"account": a, "name": ACCOUNTS.get(a, a), "debit": str(q2(v)), "credit": "0.00",
                "cost_center": cost_center} for a, v in sorted(debit.items())]
    entries += [{"account": a, "name": ACCOUNTS[a], "debit": str(q2(v)), "credit": "0.00", "cost_center": None}
                for a, v in sorted(vat.items())]
    entries.append({"account": PAYABLES_ACCOUNT, "name": ACCOUNTS[PAYABLES_ACCOUNT], "debit": "0.00",
                    "credit": str(q2(sum(debit.values()) + sum(vat.values()))), "cost_center": None,
                    "partner_pib": inv.supplier_pib})

    confidence = min(c.confidence for c in cls) if cls else 0.0
    source = "llm" if any(c.source == "llm" for c in cls) else ("rules" if any(c.source == "rules" for c in cls) else "master")
    rationale = "; ".join(f"st. {ln.line_no} -> {c.account} ({c.reason})" for ln, c in zip(inv.lines, cls))
    for p in list(inv.proposals):
        session.delete(p)
    prop = BookingProposal(invoice_id=inv.id, entries=entries, source=source, confidence=round(confidence, 2),
                           rationale=rationale)
    session.add(prop)
    session.flush()
    prop.line_accounts = [c.account for c in cls]  # transient, used by evaluation
    return prop
