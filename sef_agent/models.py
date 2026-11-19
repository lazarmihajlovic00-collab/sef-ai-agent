"""Domain model: master data, procurement documents, e-invoices, findings,
booking proposals, approval requests and a hash-chained audit log."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

Money = Numeric(18, 2)
Qty = Numeric(18, 3)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InvoiceStatus(str, Enum):
    NEW = "NEW"                  # pulled from SEF, not processed yet
    READY = "READY"              # matched cleanly, booking proposal waiting for approval
    EXCEPTION = "EXCEPTION"      # has blocking findings, needs a person
    APPROVED = "APPROVED"        # booking approved by a person
    REJECTED = "REJECTED"        # rejected by a person (and in SEF)


class FindingType(str, Enum):
    DUPLICATE = "DUPLICATE"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
    UNKNOWN_SUPPLIER = "UNKNOWN_SUPPLIER"
    INVALID_PIB = "INVALID_PIB"
    BANK_ACCOUNT_CHANGED = "BANK_ACCOUNT_CHANGED"
    NO_PO = "NO_PO"
    PO_SUPPLIER_MISMATCH = "PO_SUPPLIER_MISMATCH"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    PRICE_WITHIN_TOLERANCE = "PRICE_WITHIN_TOLERANCE"
    QTY_NOT_RECEIVED = "QTY_NOT_RECEIVED"
    ITEM_NOT_ON_PO = "ITEM_NOT_ON_PO"
    VAT_MISMATCH = "VAT_MISMATCH"
    TOTAL_MISMATCH = "TOTAL_MISMATCH"


class Severity(str, Enum):
    BLOCK = "BLOCK"
    WARN = "WARN"
    INFO = "INFO"


class ActionStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"


# ---------------------------------------------------------------- master data
class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    pib: Mapped[str] = mapped_column(String(9), unique=True, index=True)
    mb: Mapped[str] = mapped_column(String(8))
    city: Mapped[str] = mapped_column(String(80))
    bank_account: Mapped[str] = mapped_column(String(30))
    category: Mapped[str] = mapped_column(String(40))  # goods / services / utilities ...
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)


class Item(Base):
    __tablename__ = "items"
    code: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(10))
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    gl_account: Mapped[str] = mapped_column(String(10))  # konto (Serbian chart of accounts)


# ---------------------------------------------------------------- procurement
class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    order_date: Mapped[date] = mapped_column(Date)
    cost_center: Mapped[str] = mapped_column(String(20))
    supplier: Mapped[Supplier] = relationship()
    lines: Mapped[list["POLine"]] = relationship(back_populates="po", cascade="all, delete-orphan")


class POLine(Base):
    __tablename__ = "po_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"))
    item_code: Mapped[str] = mapped_column(ForeignKey("items.code"))
    qty: Mapped[Decimal] = mapped_column(Qty)
    unit_price: Mapped[Decimal] = mapped_column(Money)
    po: Mapped[PurchaseOrder] = relationship(back_populates="lines")


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"))
    receipt_date: Mapped[date] = mapped_column(Date)
    lines: Mapped[list["GRLine"]] = relationship(back_populates="gr", cascade="all, delete-orphan")


class GRLine(Base):
    __tablename__ = "gr_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    gr_id: Mapped[int] = mapped_column(ForeignKey("goods_receipts.id"))
    item_code: Mapped[str] = mapped_column(ForeignKey("items.code"))
    qty: Mapped[Decimal] = mapped_column(Qty)
    gr: Mapped[GoodsReceipt] = relationship(back_populates="lines")


# ---------------------------------------------------------------- e-invoices
class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    sef_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    number: Mapped[str] = mapped_column(String(60), index=True)
    supplier_pib: Mapped[str] = mapped_column(String(20), index=True)
    supplier_name: Mapped[str] = mapped_column(String(200))
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    bank_account: Mapped[str | None] = mapped_column(String(40), nullable=True)
    issue_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    order_reference: Mapped[str | None] = mapped_column(String(60), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="RSD")
    net_amount: Mapped[Decimal] = mapped_column(Money)
    vat_amount: Mapped[Decimal] = mapped_column(Money)
    total_amount: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(20), default=InvoiceStatus.NEW.value, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    raw_xml: Mapped[str] = mapped_column(Text)
    supplier: Mapped[Supplier | None] = relationship()
    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceLine.line_no"
    )
    findings: Mapped[list["Finding"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    proposals: Mapped[list["BookingProposal"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    line_no: Mapped[int] = mapped_column(Integer)
    item_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    description: Mapped[str] = mapped_column(String(300))
    qty: Mapped[Decimal] = mapped_column(Qty)
    unit: Mapped[str] = mapped_column(String(10))
    unit_price: Mapped[Decimal] = mapped_column(Money)
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    net_amount: Mapped[Decimal] = mapped_column(Money)
    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    paid_on: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)


# ---------------------------------------------------------------- agent output
class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    line_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str] = mapped_column(String(500))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    invoice: Mapped[Invoice] = relationship(back_populates="findings")


class BookingProposal(Base):
    __tablename__ = "booking_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    entries: Mapped[list] = mapped_column(JSON)  # [{account, name, debit, credit, cost_center}]
    source: Mapped[str] = mapped_column(String(20))  # rules | llm
    confidence: Mapped[float] = mapped_column()
    rationale: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    invoice: Mapped[Invoice] = relationship(back_populates="proposals")


class ActionRequest(Base):
    """Anything that changes state outside of analysis must be approved by a person."""

    __tablename__ = "action_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))  # APPROVE_BOOKING | ACCEPT_IN_SEF | REJECT_IN_SEF
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text)
    proposed_by: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(10), default=ActionStatus.PENDING.value, index=True)
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    """Append-only, hash-chained log: each row stores the hash of the previous row,
    so editing or deleting history is detectable (see audit.verify_chain)."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(80))
    event: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class LLMCall(Base):
    """Token usage per LLM call, for cost-per-document reporting."""

    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    purpose: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(60))
    invoice_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column()


__all__ = [n for n in dir() if n[0].isupper()]
_ = UniqueConstraint  # kept for future composite constraints
