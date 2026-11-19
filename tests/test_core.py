import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from sef_agent import audit
from sef_agent.agent import queries
from sef_agent.models import ActionRequest, AuditEvent, Invoice
from sef_agent.pipeline import ApprovalError, decide
from sef_agent.sef.client import MockSefClient
from sef_agent.sef.ubl import UblInvoice, UblLine, UblParty, build_invoice_xml, parse_invoice_xml
from sef_agent.sef.validators import is_valid_bank_account, is_valid_pib, normalize_bank_account


def test_pib_checksum():
    assert is_valid_pib("100000024")  # check digit computed by ISO 7064 MOD 11,10
    assert not is_valid_pib("100000025")
    assert not is_valid_pib("12345")
    assert is_valid_pib("RS100000024")


def test_bank_account():
    assert normalize_bank_account("160-5100-1") == "160-0000000005100-01"
    assert not is_valid_bank_account("160-0000000005100-01")
    from sef_agent.sef.validators import bank_account_check

    acc = "160-0000000005100-" + bank_account_check("1600000000005100")
    assert is_valid_bank_account(acc)


def test_ubl_roundtrip():
    sup = UblParty("Test d.o.o.", "100000024", "20000000", "Beograd")
    cus = UblParty("Kupac d.o.o.", "109876543", "21876543", "Smederevo")
    inv = UblInvoice("T-1", date(2026, 11, 1), date(2026, 12, 1), sup, cus,
                     [UblLine(1, "Brasno", Decimal("10"), Decimal("1450.00"), Decimal("10"), item_code="AL-1001"),
                      UblLine(2, "Secer", Decimal("3"), Decimal("119.00"), Decimal("20"))],
                     order_reference="PO-1", bank_account="160-0000000005100-01")
    p = parse_invoice_xml(build_invoice_xml(inv))
    assert p.number == "T-1" and p.supplier_pib == "100000024" and p.order_reference == "PO-1"
    assert p.net_amount == Decimal("14857.00")
    assert p.vat_amount == Decimal("1450.00") + Decimal("71.40")
    assert p.total_amount == p.net_amount + p.vat_amount
    assert [ln.item_code for ln in p.lines] == ["AL-1001", None]


def test_ubl_parser_rejects_external_entities():
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'
    with pytest.raises(Exception):
        parse_invoice_xml(evil)


def test_detection_matches_ground_truth(seeded):
    truth = json.loads((seeded.data_dir / "ground_truth.json").read_text())
    invs = {str(i.sef_id): i for i in seeded.execute(select(Invoice)).scalars()}
    blocking_missed = 0
    for sid, t in truth.items():
        got = {f.type for f in invs[sid].findings}
        missing = set(t["expected_findings"]) - got
        blocking_missed += bool(missing)
    assert blocking_missed == 0  # recall must be 100% on injected problems


def test_exact_duplicate_with_reformatted_number_is_caught(seeded):
    dups = [i for i in seeded.execute(select(Invoice)).scalars() if any(f.type == "DUPLICATE" for f in i.findings)]
    assert any("/" in i.number for i in dups)
    assert all(i.status == "EXCEPTION" for i in dups)


def test_approval_needs_named_person(seeded):
    ar = seeded.execute(select(ActionRequest).where(ActionRequest.status == "PENDING")).scalars().first()
    with pytest.raises(ApprovalError):
        decide(seeded, ar.id, True, user="agent")
    sef = MockSefClient(seeded.data_dir / "sef_inbox")
    decide(seeded, ar.id, True, user="ana.petrovic", comment="ok", sef=sef)
    inv = seeded.get(Invoice, ar.invoice_id)
    assert inv.status == "APPROVED"
    idx = json.loads((seeded.data_dir / "sef_inbox" / "_index.json").read_text())
    assert idx[str(inv.sef_id)]["status"] == "Approved"
    with pytest.raises(ApprovalError):
        decide(seeded, ar.id, False, user="ana.petrovic")


def test_audit_chain_detects_tampering(seeded):
    assert audit.verify_chain(seeded) == (True, None)
    ev = seeded.execute(select(AuditEvent).order_by(AuditEvent.id).offset(5).limit(1)).scalar_one()
    ev.data = {**ev.data, "total": "1.00"}
    seeded.flush()
    ok, bad = audit.verify_chain(seeded)
    assert not ok and bad == ev.id


def test_payables_excludes_duplicates_and_paid(seeded):
    r = queries.payables(seeded)
    numbers = [x["number"] for x in r["invoices"]]
    assert all("/" not in n for n in numbers)  # reformatted duplicates are not liabilities
    total = sum(Decimal(x["open"]) for x in r["invoices"])
    if not r["truncated"]:
        assert total == Decimal(r["total_open"])
    month = queries.payables(seeded, due_until="2026-11-30")
    assert Decimal(month["total_open"]) <= Decimal(r["total_open"])


def test_supplier_fuzzy_search(seeded):
    assert queries.find_suppliers(seeded, "agro lux")[0].name == "Agro Lux d.o.o."
    assert queries.find_suppliers(seeded, "Tehno centar")


@pytest.mark.parametrize("q", ["DELETE FROM invoices", "select 1; drop table invoices",
                               "UPDATE invoices SET status='APPROVED'", "pragma table_info(invoices)"])
def test_run_sql_is_read_only(seeded, q):
    assert "error" in queries.run_sql(seeded, q)


def test_run_sql_select(seeded):
    r = queries.run_sql(seeded, "SELECT status, count(*) FROM invoices GROUP BY status")
    assert r["columns"] == ["status", "count(*)"] and r["rows"]
