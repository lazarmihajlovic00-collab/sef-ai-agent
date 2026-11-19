"""REST API + static dashboard."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, llm
from ..agent import queries
from ..db import SessionLocal, init_db
from ..ingest import pull_from_sef
from ..models import ActionRequest, AuditEvent, Finding, Invoice, LLMCall
from ..pipeline import ApprovalError, decide, default_classifier, process_invoice
from ..roi import RoiInputs, compute_roi
from ..sef.client import make_client

STATIC = Path(__file__).resolve().parents[2] / "web" / "dist"
EVAL_JSON = Path("docs/eval_report.json")


@asynccontextmanager
async def lifespan(_app):
    init_db()
    if os.environ.get("SEF_MODE", "mock") == "mock":
        os.environ.setdefault("AS_OF_DATE", "2026-11-19")
    yield


app = FastAPI(title="SEF AI Agent", version="0.1.0", lifespan=lifespan)


def db():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def current_user(x_user: str | None = Header(default=None)) -> str:
    # Demo: the dashboard sends the reviewer's name. Production would use SSO / OIDC here.
    return (x_user or "demo.korisnik").strip()[:80]


def _inv_row(inv: Invoice) -> dict:
    return {"id": inv.id, "sef_id": inv.sef_id, "number": inv.number, "supplier": inv.supplier_name,
            "issue_date": inv.issue_date.isoformat(), "due_date": inv.due_date.isoformat() if inv.due_date else None,
            "total": str(inv.total_amount), "status": inv.status,
            "findings": [{"type": f.type, "severity": f.severity} for f in inv.findings]}


@app.get("/api/summary")
def summary(s: Session = Depends(db)):
    by_status = dict(s.execute(select(Invoice.status, func.count()).group_by(Invoice.status)).all())
    by_type = dict(s.execute(select(Finding.type, func.count()).group_by(Finding.type)).all())
    pending = s.execute(select(func.count()).select_from(ActionRequest).where(ActionRequest.status == "PENDING")).scalar()
    pay = queries.payables(s)
    month_end = queries.as_of().replace(day=28)
    while True:
        nxt = date.fromordinal(month_end.toordinal() + 1)
        if nxt.month != month_end.month:
            break
        month_end = nxt
    pay_month = queries.payables(s, due_until=month_end.isoformat())
    llm_cost = s.execute(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0))).scalar()
    return {"as_of": queries.as_of().isoformat(), "by_status": by_status, "findings_by_type": by_type,
            "pending_actions": pending, "open_payables": pay["total_open"],
            "open_in_exception": pay["of_which_in_exception"], "due_this_month": pay_month["total_open"],
            "llm_enabled": llm.llm_available(), "llm_cost_usd": round(float(llm_cost), 4)}


@app.get("/api/invoices")
def invoices(status: str | None = None, s: Session = Depends(db)):
    stmt = select(Invoice).order_by(Invoice.issue_date.desc(), Invoice.id.desc())
    if status:
        stmt = stmt.where(Invoice.status == status)
    return [_inv_row(i) for i in s.execute(stmt).scalars()]


@app.get("/api/invoices/{inv_id}")
def invoice(inv_id: int, s: Session = Depends(db)):
    inv = s.get(Invoice, inv_id)
    if not inv:
        raise HTTPException(404)
    d = queries.get_invoice(s, str(inv.sef_id))
    d["id"] = inv.id
    d["bank_account"] = inv.bank_account
    d["master_bank_account"] = inv.supplier.bank_account if inv.supplier else None
    d["history"] = [{"ts": e.ts.isoformat(), "actor": e.actor, "event": e.event, "data": e.data}
                    for e in s.execute(select(AuditEvent).where(AuditEvent.entity == "invoice",
                                                                AuditEvent.entity_id == str(inv.id))
                                       .order_by(AuditEvent.id)).scalars()]
    return d


@app.get("/api/invoices/{inv_id}/xml")
def invoice_xml(inv_id: int, s: Session = Depends(db)):
    inv = s.get(Invoice, inv_id)
    if not inv:
        raise HTTPException(404)
    return {"xml": inv.raw_xml}


@app.post("/api/invoices/{inv_id}/reprocess")
def reprocess(inv_id: int, s: Session = Depends(db), user: str = Depends(current_user)):
    inv = s.get(Invoice, inv_id)
    if not inv or inv.status in ("APPROVED", "REJECTED"):
        raise HTTPException(400, "invoice not found or already decided")
    audit.log(s, user, "invoice.reprocess_requested", "invoice", inv.id)
    process_invoice(s, inv, default_classifier(s))
    return _inv_row(inv)


@app.post("/api/sync")
def sync(s: Session = Depends(db), user: str = Depends(current_user)):
    client = make_client()
    new = pull_from_sef(s, client, date(2026, 1, 1), queries.as_of())
    clf = default_classifier(s)
    for inv in sorted(new, key=lambda i: i.sef_id):
        process_invoice(s, inv, clf)
    audit.log(s, user, "sef.sync", "system", "sef", {"new": len(new)})
    return {"new": len(new)}


@app.get("/api/actions")
def actions(status: str = "PENDING", s: Session = Depends(db)):
    rows = s.execute(select(ActionRequest, Invoice).join(Invoice, Invoice.id == ActionRequest.invoice_id)
                     .where(ActionRequest.status == status)
                     .order_by(ActionRequest.action.desc(), ActionRequest.id)).all()
    return [{"id": a.id, "action": a.action, "reason": a.reason, "proposed_by": a.proposed_by, "status": a.status,
             "decided_by": a.decided_by, "invoice": _inv_row(i),
             "confidence": i.proposals[-1].confidence if i.proposals else None} for a, i in rows]


class Decision(BaseModel):
    approve: bool
    comment: str = ""


@app.post("/api/actions/{action_id}/decide")
def decide_action(action_id: int, body: Decision, s: Session = Depends(db), user: str = Depends(current_user)):
    try:
        ar = decide(s, action_id, body.approve, user, body.comment, sef=make_client())
    except ApprovalError as e:
        raise HTTPException(400, str(e))
    return {"id": ar.id, "status": ar.status, "decided_by": ar.decided_by}


class ChatIn(BaseModel):
    question: str
    history: list = []


@app.post("/api/chat")
def chat(body: ChatIn, s: Session = Depends(db), user: str = Depends(current_user)):
    from ..agent.runner import ask

    res = ask(s, body.question, user=user)
    audit.log(s, user, "chat.question", "chat", "-", {"question": body.question,
                                                      "tools": [t["name"] for t in res.tool_calls]})
    return {"answer": res.answer, "tool_calls": res.tool_calls, "cost_usd": round(res.cost_usd, 5),
            "latency_ms": res.latency_ms}


@app.get("/api/audit")
def audit_log(limit: int = 200, s: Session = Depends(db)):
    evs = s.execute(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)).scalars()
    return [{"id": e.id, "ts": e.ts.isoformat(), "actor": e.actor, "event": e.event, "entity": e.entity,
             "entity_id": e.entity_id, "data": e.data, "hash": e.hash[:12]} for e in evs]


@app.get("/api/audit/verify")
def audit_verify(s: Session = Depends(db)):
    ok, bad = audit.verify_chain(s)
    return {"ok": ok, "first_bad_id": bad}


@app.get("/api/roi")
def roi(invoices_per_month: int = 400, manual_minutes_per_invoice: float = 12,
        accountant_cost_rsd_per_hour: float = 1250, s: Session = Depends(db)):
    n = s.execute(select(func.count()).select_from(Invoice)).scalar() or 1
    ready = s.execute(select(func.count()).select_from(Invoice).where(Invoice.status != "EXCEPTION")).scalar()
    per_inv_usd = 0.0
    if EVAL_JSON.exists():
        rep = json.loads(EVAL_JSON.read_text(encoding="utf-8"))
        c = rep["booking"]["classifiers"].get("llm", {})
        per_inv_usd = c.get("cost_per_line_usd", 0) * rep["booking"]["free_text_lines"] / max(rep["detection"]["invoices"], 1)
    inp = RoiInputs(invoices_per_month=invoices_per_month, manual_minutes_per_invoice=manual_minutes_per_invoice,
                    accountant_cost_rsd_per_hour=accountant_cost_rsd_per_hour)
    return compute_roi(inp, auto_share=ready / n, llm_cost_per_invoice_usd=per_inv_usd)


@app.get("/api/eval")
def eval_report():
    if not EVAL_JSON.exists():
        raise HTTPException(404, "run `sef-agent eval` first")
    return json.loads(EVAL_JSON.read_text(encoding="utf-8"))


if STATIC.exists():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        return FileResponse(STATIC / "index.html")

_ = Decimal
