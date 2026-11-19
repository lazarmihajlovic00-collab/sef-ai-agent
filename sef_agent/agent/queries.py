"""Read-only query functions used as agent tools (and by the API and evaluation).

The agent never computes money itself: it calls these functions, which run SQL,
and it reports their numbers. That is what makes answers checkable."""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..models import ActionRequest, Finding, Invoice, Payment, Supplier

EXCLUDED_FROM_PAYABLES = {"DUPLICATE", "UNKNOWN_SUPPLIER"}


def as_of() -> date:
    v = os.environ.get("AS_OF_DATE")
    return date.fromisoformat(v) if v else date.today()


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower()).replace("đ", "dj")
    return "".join(c for c in s if not unicodedata.combining(c))


def find_suppliers(session: Session, query: str) -> list[Supplier]:
    q = _norm(query).replace("d.o.o.", "").replace("doo", "").strip()
    words = [w for w in re.split(r"\W+", q) if len(w) > 2]
    out = []
    for s in session.execute(select(Supplier)).scalars():
        name = _norm(s.name)
        if q and (q in name or s.pib == query.strip() or (words and all(w in name for w in words))):
            out.append(s)
    return out


def _paid(session: Session, invoice_ids: list[int]) -> dict[int, Decimal]:
    if not invoice_ids:
        return {}
    rows = session.execute(select(Payment.invoice_id, func.sum(Payment.amount))
                           .where(Payment.invoice_id.in_(invoice_ids)).group_by(Payment.invoice_id)).all()
    return {i: Decimal(str(a)) for i, a in rows}


def payables(session: Session, supplier_query: str | None = None, due_until: str | None = None,
             overdue_only: bool = False) -> dict:
    """Open (unpaid) amounts owed to suppliers."""
    stmt = select(Invoice).where(Invoice.status != "REJECTED")
    matched = None
    if supplier_query:
        matched = find_suppliers(session, supplier_query)
        if not matched:
            return {"error": f"nema dobavljaca koji odgovara upitu '{supplier_query}'"}
        stmt = stmt.where(Invoice.supplier_id.in_([s.id for s in matched]))
    limit_date = as_of() if overdue_only else (date.fromisoformat(due_until) if due_until else None)
    if limit_date:
        stmt = stmt.where(Invoice.due_date <= limit_date) if not overdue_only else stmt.where(Invoice.due_date < limit_date)
    invs = session.execute(stmt.order_by(Invoice.due_date)).scalars().all()
    paid = _paid(session, [i.id for i in invs])
    rows, total, disputed = [], Decimal("0"), Decimal("0")
    for inv in invs:
        types = {f.type for f in inv.findings}
        if types & EXCLUDED_FROM_PAYABLES:
            continue
        open_amt = inv.total_amount - paid.get(inv.id, Decimal("0"))
        if open_amt <= 0:
            continue
        total += open_amt
        if inv.status == "EXCEPTION":
            disputed += open_amt
        rows.append({"number": inv.number, "supplier": inv.supplier_name, "due_date": inv.due_date.isoformat(),
                     "open": str(open_amt), "status": inv.status})
    return {
        "as_of": as_of().isoformat(), "due_until": limit_date.isoformat() if limit_date else None,
        "suppliers_matched": [s.name for s in matched] if matched else None,
        "total_open": str(total), "of_which_in_exception": str(disputed), "count": len(rows),
        "invoices": rows[:40], "truncated": len(rows) > 40,
    }


def spend_by_supplier(session: Session, date_from: str, date_to: str, limit: int = 10) -> dict:
    rows = session.execute(
        select(Invoice.supplier_name, func.sum(Invoice.net_amount), func.count())
        .where(Invoice.issue_date >= date.fromisoformat(date_from), Invoice.issue_date <= date.fromisoformat(date_to),
               Invoice.status != "REJECTED", ~Invoice.findings.any(Finding.type.in_(EXCLUDED_FROM_PAYABLES)))
        .group_by(Invoice.supplier_name).order_by(func.sum(Invoice.net_amount).desc()).limit(limit)
    ).all()
    return {"from": date_from, "to": date_to,
            "rows": [{"supplier": n, "net_spend": f"{Decimal(str(a)):.2f}", "invoices": c} for n, a, c in rows]}


def list_exceptions(session: Session, finding_type: str | None = None, supplier_query: str | None = None) -> dict:
    stmt = select(Invoice).where(Invoice.status == "EXCEPTION")
    if supplier_query:
        ids = [s.id for s in find_suppliers(session, supplier_query)]
        stmt = stmt.where(Invoice.supplier_id.in_(ids))
    out = []
    for inv in session.execute(stmt.order_by(Invoice.issue_date.desc())).scalars():
        fs = [f for f in inv.findings if not finding_type or f.type == finding_type]
        if not fs:
            continue
        out.append({"number": inv.number, "sef_id": inv.sef_id, "supplier": inv.supplier_name,
                    "total": str(inv.total_amount), "issue_date": inv.issue_date.isoformat(),
                    "findings": [{"type": f.type, "severity": f.severity, "message": f.message} for f in inv.findings]})
    return {"count": len(out), "invoices": out[:30], "truncated": len(out) > 30}


def get_invoice(session: Session, number: str) -> dict:
    inv =session.execute(select(Invoice).where(Invoice.number == number.strip()).order_by(Invoice.sef_id)).scalars().first()
    if inv is None and number.strip().isdigit():
        inv = session.execute(select(Invoice).where(Invoice.sef_id == int(number.strip()))).scalar_one_or_none()
    if inv is None:
        return {"error": f"faktura {number} nije pronadjena"}
    paid = _paid(session, [inv.id]).get(inv.id, Decimal("0"))
    prop = inv.proposals[-1] if inv.proposals else None
    pending = session.execute(select(ActionRequest).where(ActionRequest.invoice_id == inv.id)).scalars().all()
    return {
        "number": inv.number, "sef_id": inv.sef_id, "supplier": inv.supplier_name, "pib": inv.supplier_pib,
        "issue_date": inv.issue_date.isoformat(), "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "order_reference": inv.order_reference, "net": str(inv.net_amount), "vat": str(inv.vat_amount),
        "total": str(inv.total_amount), "paid": str(paid), "status": inv.status,
        "lines": [{"no": l.line_no, "item_code": l.item_code, "description": l.description, "qty": str(l.qty),
                   "unit_price": str(l.unit_price), "net": str(l.net_amount)} for l in inv.lines],
        "findings": [{"type": f.type, "severity": f.severity, "message": f.message} for f in inv.findings],
        "booking_proposal": {"entries": prop.entries, "confidence": prop.confidence, "rationale": prop.rationale} if prop else None,
        "actions": [{"id": a.id, "action": a.action, "status": a.status, "decided_by": a.decided_by} for a in pending],
    }


# ---------------------------------------------------------------- guarded SQL
_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|pragma|replace|vacuum|grant|truncate)\b", re.I)

SCHEMA_DOC = """\
suppliers(id, name, pib, mb, city, bank_account, category, payment_terms_days)
items(code, name, unit, vat_rate, gl_account)
purchase_orders(id, number, supplier_id, order_date, cost_center)
po_lines(id, po_id, item_code, qty, unit_price)
goods_receipts(id, number, po_id, receipt_date)   gr_lines(id, gr_id, item_code, qty)
invoices(id, sef_id, number, supplier_pib, supplier_name, supplier_id, bank_account, issue_date, due_date,
         order_reference, currency, net_amount, vat_amount, total_amount, status[NEW|READY|EXCEPTION|APPROVED|REJECTED])
invoice_lines(id, invoice_id, line_no, item_code, description, qty, unit, unit_price, vat_rate, net_amount)
payments(id, invoice_id, paid_on, amount)
findings(id, invoice_id, type, severity, line_no, message)
booking_proposals(id, invoice_id, entries(json), source, confidence, rationale)"""


def run_sql(session: Session, query: str, max_rows: int = 50) -> dict:
    q = query.strip().rstrip(";")
    if ";" in q or not re.match(r"^(select|with)\b", q, re.I) or _FORBIDDEN.search(q):
        return {"error": "dozvoljen je samo jedan SELECT upit"}
    try:
        with session.begin_nested() as sp:
            res = session.execute(text(q))
            cols = list(res.keys())
            rows = [list(map(lambda v: str(v) if v is not None else None, r)) for r in res.fetchmany(max_rows + 1)]
            sp.rollback()
    except Exception as e:  # report SQL errors back to the model so it can fix the query
        return {"error": f"SQL greska: {str(e).splitlines()[0][:300]}"}
    return {"columns": cols, "rows": rows[:max_rows], "truncated": len(rows) > max_rows}
