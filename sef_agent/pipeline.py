"""End-to-end processing of an incoming invoice, and the human approval step.

Decision D2: the agent only proposes. Every booking and every accept/reject in SEF
is an ActionRequest that a named person approves or denies; both outcomes are audited.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .booking.classifier import LLMClassifier, RuleClassifier
from .booking.proposer import propose_booking
from .matching.engine import run_matching
from .models import ActionRequest, ActionStatus, Invoice, InvoiceStatus, utcnow
from .sef.client import SefClient

AUTO_REJECT_TYPES = {"DUPLICATE", "UNKNOWN_SUPPLIER"}


def default_classifier(session: Session):
    from . import llm

    return LLMClassifier(session=session) if llm.llm_available() else RuleClassifier()


def process_invoice(session: Session, inv: Invoice, classifier=None) -> Invoice:
    findings = run_matching(session, inv)
    audit.log(session, "agent", "invoice.checked", "invoice", inv.id,
              {"status": inv.status, "findings": [f.type for f in findings]})

    # remove stale pending requests before proposing again
    for ar in session.execute(select(ActionRequest).where(ActionRequest.invoice_id == inv.id,
                                                          ActionRequest.status == ActionStatus.PENDING.value)).scalars():
        session.delete(ar)

    types = {f.type for f in findings}
    if types & AUTO_REJECT_TYPES:
        reason = "; ".join(f.message for f in findings if f.type in AUTO_REJECT_TYPES)
        session.add(ActionRequest(invoice_id=inv.id, action="REJECT_IN_SEF", proposed_by="agent",
                                  reason=reason, payload={"comment": reason[:250]}))
        audit.log(session, "agent", "action.proposed", "invoice", inv.id, {"action": "REJECT_IN_SEF"})
        return inv

    prop = propose_booking(session, inv, classifier or default_classifier(session))
    audit.log(session, "agent", "booking.proposed", "invoice", inv.id,
              {"proposal_id": prop.id, "source": prop.source, "confidence": prop.confidence})
    if inv.status == InvoiceStatus.READY.value:
        warn = [f.message for f in findings]
        session.add(ActionRequest(
            invoice_id=inv.id, action="APPROVE_BOOKING", proposed_by="agent",
            reason="3-way match uspesan" + (f" uz napomene: {'; '.join(warn)}" if warn else "") + ".",
            payload={"proposal_id": prop.id, "accept_in_sef": True},
        ))
        audit.log(session, "agent", "action.proposed", "invoice", inv.id, {"action": "APPROVE_BOOKING"})
    session.flush()
    return inv


class ApprovalError(RuntimeError):
    pass


def decide(session: Session, action_id: int, approve: bool, user: str, comment: str = "",
           sef: SefClient | None = None) -> ActionRequest:
    ar = session.get(ActionRequest, action_id)
    if ar is None:
        raise ApprovalError("action not found")
    if ar.status != ActionStatus.PENDING.value:
        raise ApprovalError(f"action already {ar.status}")
    if not user or user.startswith("agent"):
        raise ApprovalError("a named person must decide")
    inv = session.get(Invoice, ar.invoice_id)
    ar.status = ActionStatus.APPROVED.value if approve else ActionStatus.DENIED.value
    ar.decided_by, ar.decided_at, ar.comment = user, utcnow(), comment
    audit.log(session, user, "action.approved" if approve else "action.denied", "invoice", inv.id,
              {"action_id": ar.id, "action": ar.action, "comment": comment})
    if not approve:
        return ar

    if ar.action == "APPROVE_BOOKING":
        inv.status = InvoiceStatus.APPROVED.value
        if sef is not None and ar.payload.get("accept_in_sef"):
            res = sef.accept_reject(inv.sef_id, True, comment or "Prihvaceno")
            audit.log(session, user, "sef.accepted", "invoice", inv.id, {"sef_response": res})
    elif ar.action == "REJECT_IN_SEF":
        inv.status = InvoiceStatus.REJECTED.value
        if sef is not None:
            res = sef.accept_reject(inv.sef_id, False, comment or ar.payload.get("comment", "Odbijeno"))
            audit.log(session, user, "sef.rejected", "invoice", inv.id, {"sef_response": res})
    session.flush()
    return ar


def request_rejection(session: Session, inv: Invoice, user: str, reason: str) -> ActionRequest:
    """A person (or the chat agent on their behalf) asks to reject an exception invoice; still needs approval."""
    ar = ActionRequest(invoice_id=inv.id, action="REJECT_IN_SEF", proposed_by=user, reason=reason,
                       payload={"comment": reason[:250]})
    session.add(ar)
    session.flush()
    audit.log(session, user, "action.proposed", "invoice", inv.id, {"action": "REJECT_IN_SEF", "reason": reason})
    return ar
