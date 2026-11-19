"""Pull incoming invoices from SEF into the database, and import bank payments."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .models import Invoice, InvoiceLine, Payment, Supplier
from .sef.client import SefClient
from .sef.ubl import parse_invoice_xml


def pull_from_sef(session: Session, client: SefClient, date_from: date, date_to: date) -> list[Invoice]:
    known = set(session.execute(select(Invoice.sef_id)).scalars())
    new: list[Invoice] = []
    for sef_id in client.list_incoming_ids(date_from, date_to):
        if sef_id in known:
            continue
        xml = client.get_invoice_xml(sef_id)
        p = parse_invoice_xml(xml)
        supplier = session.execute(select(Supplier).where(Supplier.pib == p.supplier_pib)).scalar_one_or_none()
        inv = Invoice(
            sef_id=sef_id, number=p.number, supplier_pib=p.supplier_pib, supplier_name=p.supplier_name,
            supplier_id=supplier.id if supplier else None, bank_account=p.bank_account,
            issue_date=p.issue_date, due_date=p.due_date, order_reference=p.order_reference,
            currency=p.currency, net_amount=p.net_amount, vat_amount=p.vat_amount,
            total_amount=p.total_amount, raw_xml=xml,
        )
        for ln in p.lines:
            inv.lines.append(InvoiceLine(
                line_no=ln.line_no, item_code=ln.item_code, description=ln.description, qty=ln.qty,
                unit=ln.unit, unit_price=ln.unit_price, vat_rate=ln.vat_rate, net_amount=ln.net_amount,
            ))
        session.add(inv)
        session.flush()
        audit.log(session, "system:sef-sync", "invoice.received", "invoice", inv.id,
                  {"sef_id": sef_id, "number": p.number, "supplier_pib": p.supplier_pib, "total": p.total_amount})
        new.append(inv)
    return new


def import_bank_statement(session: Session, path: Path) -> int:
    """Match payments to invoices by supplier PIB + invoice number (poziv na broj)."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    n = 0
    for r in rows:
        inv = session.execute(
            select(Invoice).where(Invoice.supplier_pib == r["payee_pib"], Invoice.number == r["reference"])
            .order_by(Invoice.sef_id)
        ).scalars().first()
        if inv is None:
            continue
        exists = session.execute(select(Payment).where(Payment.invoice_id == inv.id)).first()
        if exists:
            continue
        session.add(Payment(invoice_id=inv.id, paid_on=date.fromisoformat(r["date"]), amount=Decimal(r["amount"])))
        n += 1
    session.flush()
    audit.log(session, "system:bank-import", "payments.imported", "bank_statement", Path(path).name, {"count": n})
    return n
