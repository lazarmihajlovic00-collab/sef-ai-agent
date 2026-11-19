"""Synthetic but realistic dataset for a Serbian trading company.

Creates master data (suppliers, items), purchase orders, goods receipts, a folder of
UBL e-invoices served by the mock SEF, a bank statement with payments, and a
ground-truth file that says, for every invoice, which problems were injected and
which account each line belongs to. The evaluation compares the agent to that file.

All company names are fictional. PIBs and bank accounts have valid check digits.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import GoodsReceipt, GRLine, Item, POLine, PurchaseOrder, Supplier
from ..sef.ubl import UblInvoice, UblLine, UblParty, build_invoice_xml, q2
from ..sef.validators import bank_account_check, pib_check_digit

TODAY = date(2026, 11, 19)
START = date(2026, 8, 25)


def make_pib(rng: random.Random) -> str:
    first8 = f"{rng.randint(10000000, 11499999)}"
    return first8 + str(pib_check_digit(first8))


def make_account(rng: random.Random, bank: str) -> str:
    mid = f"{rng.randint(10**9, 10**13 - 1):013d}"
    return f"{bank}-{mid}-{bank_account_check(bank + mid)}"


@dataclass
class SupplierSpec:
    key: str
    name: str
    city: str
    category: str          # goods / mixed / services
    needs_po: bool
    terms: int
    items: list[tuple]     # (code, name, unit, vat, account, price)
    service_lines: list[tuple] = ()  # (description, account, price, vat) for free-text invoices


SUPPLIERS: list[SupplierSpec] = [
    SupplierSpec("agro", "Agro Lux d.o.o.", "Novi Sad", "goods", True, 45, [
        ("AL-1001", "Brasno T-400 25kg", "H87", 10, "1320", Decimal("1450.00")),
        ("AL-1002", "Secer kristal 1kg", "H87", 20, "1320", Decimal("119.00")),
        ("AL-1003", "Ulje suncokretovo 1l", "H87", 20, "1320", Decimal("189.00")),
        ("AL-1004", "Kukuruzni griz 5kg", "H87", 10, "1320", Decimal("540.00")),
    ]),
    SupplierSpec("napici", "Dunavski Napici d.o.o.", "Smederevo", "goods", True, 30, [
        ("DN-200", "Mineralna voda 1.5l pak 6", "H87", 20, "1320", Decimal("312.00")),
        ("DN-210", "Sok jabuka 1l", "H87", 20, "1320", Decimal("145.00")),
        ("DN-220", "Ledeni caj breskva 0.5l", "H87", 20, "1320", Decimal("98.00")),
    ]),
    SupplierSpec("mlecni", "Ravnica Mlecni Proizvodi d.o.o.", "Zrenjanin", "goods", True, 15, [
        ("RM-01", "Mleko 2.8% 1l", "H87", 10, "1320", Decimal("132.00")),
        ("RM-02", "Jogurt 1kg", "H87", 10, "1320", Decimal("168.00")),
        ("RM-03", "Sir gauda kg", "KGM", 10, "1320", Decimal("890.00")),
    ]),
    SupplierSpec("ambalaza", "Pak Forma d.o.o.", "Beograd", "goods", True, 30, [
        ("PF-10", "Kartonska kutija 40x30x30", "H87", 20, "1010", Decimal("74.00")),
        ("PF-11", "Stretch folija 23my", "H87", 20, "1010", Decimal("1290.00")),
        ("PF-12", "Etikete termo 100x150 rolna", "H87", 20, "1010", Decimal("640.00")),
    ]),
    SupplierSpec("tehno", "Tehno Centar Plus d.o.o.", "Beograd", "mixed", True, 30, [
        ("TC-500", "Toner HP 26A", "H87", 20, "5120", Decimal("6900.00")),
        ("TC-501", "Papir A4 80g ris", "H87", 20, "5120", Decimal("590.00")),
        ("TC-610", "Bezicni mis Logi M190 (za prodaju)", "H87", 20, "1320", Decimal("1150.00")),
        ("TC-611", "USB punjac 20W (za prodaju)", "H87", 20, "1320", Decimal("990.00")),
        ("TC-900", "Servis klima uredjaja", "C62", 20, "5320", Decimal("4800.00")),
    ]),
    SupplierSpec("higijena", "Higijena Pro d.o.o.", "Novi Sad", "goods", True, 30, [
        ("HP-1", "Tecni sapun 5l", "H87", 20, "5120", Decimal("820.00")),
        ("HP-2", "Papirni ubrusi 2 rolne", "H87", 20, "5120", Decimal("210.00")),
        ("HP-3", "Sredstvo za podove 1l", "H87", 20, "5120", Decimal("265.00")),
    ]),
    SupplierSpec("petrol", "Petrol Morava d.o.o.", "Smederevo", "services", False, 15, [], [
        ("Eurodizel - kartica vozni park", "5130", Decimal("41200.00"), 20),
        ("Benzin BMB 95 - sluzbena vozila", "5130", Decimal("18600.00"), 20),
    ]),
    SupplierSpec("struja", "Elektro Energija Sever d.o.o.", "Novi Sad", "services", False, 15, [], [
        ("Elektricna energija - magacin Smederevo, obracunski period", "5130", Decimal("96500.00"), 20),
        ("Elektricna energija - prodavnica Novi Sad", "5130", Decimal("38900.00"), 20),
    ]),
    SupplierSpec("kurir", "Brza Posiljka d.o.o.", "Beograd", "services", False, 15, [], [
        ("Kurirske usluge - dostava paketa kupcima", "5310", Decimal("27400.00"), 20),
        ("Medjugradski prevoz robe Smederevo-Beograd", "5310", Decimal("35000.00"), 20),
    ]),
    SupplierSpec("telekom", "TeleLink Mreze d.o.o.", "Beograd", "services", False, 20, [], [
        ("Mesecna pretplata - poslovni internet 300 Mbps", "5311", Decimal("8900.00"), 20),
        ("Mobilna telefonija - 12 poslovnih linija", "5311", Decimal("14400.00"), 20),
    ]),
    SupplierSpec("zakup", "Poslovni Prostor Podunavlje d.o.o.", "Smederevo", "services", False, 10, [], [
        ("Zakup magacinskog prostora 600 m2 - mesecna zakupnina", "5330", Decimal("240000.00"), 20),
    ]),
    SupplierSpec("reklama", "Kreativni Kvadrat d.o.o.", "Novi Sad", "services", False, 30, [], [
        ("Izrada i stampa letaka za akciju", "5350", Decimal("46000.00"), 20),
        ("Upravljanje drustvenim mrezama - mesecni paket", "5350", Decimal("60000.00"), 20),
    ]),
    SupplierSpec("softver", "Kodna Tacka d.o.o.", "Beograd", "services", False, 30, [], [
        ("Mesecna licenca ERP softvera - 10 korisnika", "5500", Decimal("55000.00"), 20),
        ("Odrzavanje i podrska POS kasa", "5320", Decimal("18000.00"), 20),
        ("Konsultantske usluge - integracija sa SEF-om", "5500", Decimal("72000.00"), 20),
    ]),
    SupplierSpec("odrzavanje", "Majstor Servis Tim d.o.o.", "Smederevo", "services", False, 30, [], [
        ("Popravka rampe na utovarnom doku", "5320", Decimal("38500.00"), 20),
        ("Redovan servis viljuskara", "5320", Decimal("22000.00"), 20),
        ("Nabavka i ugradnja LED rasvete u magacinu", "5320", Decimal("64000.00"), 20),
    ]),
    SupplierSpec("racunovodstvo", "Bilans Konsalting d.o.o.", "Beograd", "services", False, 15, [], [
        ("Knjigovodstvene usluge za mesec", "5500", Decimal("45000.00"), 20),
        ("Izrada zavrsnog racuna - avans", "5500", Decimal("30000.00"), 20),
    ]),
]

BANKS = ["160", "170", "205", "265", "325", "340", "355"]
COST_CENTERS = ["MAG-SD", "PROD-NS", "UPRAVA"]


def generate(session: Session, out_dir: Path, seed: int = 7, n_po: int = 120) -> dict:
    """Populate the DB with master data + POs/GRs, write SEF inbox, bank statement
    and ground truth. Returns a small summary."""
    rng = random.Random(seed)
    inbox = out_dir / "sef_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for f in inbox.glob("*"):
        f.unlink()

    buyer = UblParty("Demo Trgovina d.o.o.", "109876543", "21876543", "Smederevo", "Industrijska 12")

    # ---- master data
    suppliers: dict[str, Supplier] = {}
    for spec in SUPPLIERS:
        s = Supplier(
            name=spec.name, pib=make_pib(rng), mb=f"{rng.randint(20000000, 21999999)}", city=spec.city,
            bank_account=make_account(rng, rng.choice(BANKS)), category=spec.category, payment_terms_days=spec.terms,
        )
        session.add(s)
        suppliers[spec.key] = s
        for code, name, unit, vat, acc, _price in spec.items:
            session.add(Item(code=code, name=name, unit=unit, vat_rate=Decimal(vat), gl_account=acc))
    session.flush()

    truth: dict[str, dict] = {}
    statement: list[dict] = []
    sef_id = 50_000_000 + rng.randint(1000, 9000)
    sent_index: dict[str, dict] = {}
    inv_counter: dict[str, int] = {}
    issued: list[tuple[UblInvoice, SupplierSpec, int]] = []

    def next_number(spec: SupplierSpec, d: date) -> str:
        inv_counter[spec.key] = inv_counter.get(spec.key, 0) + 1
        return f"{spec.key.upper()[:3]}-{d.year}-{inv_counter[spec.key]:04d}"

    def party(spec: SupplierSpec, pib: str | None = None) -> UblParty:
        s = suppliers[spec.key]
        return UblParty(s.name, pib or s.pib, s.mb, s.city, "Glavna 1")

    def emit(inv: UblInvoice, spec: SupplierSpec | None, expected: set[str], accounts: list[str],
             sent: date, note: str = "") -> int:
        nonlocal sef_id
        sef_id += rng.randint(1, 40)
        (inbox / f"{sef_id}.xml").write_text(build_invoice_xml(inv), encoding="utf-8")
        sent_index[str(sef_id)] = {"sent": sent.isoformat(), "status": "Seen"}
        truth[str(sef_id)] = {
            "number": inv.number, "supplier": inv.supplier.name, "supplier_pib": inv.supplier.pib,
            "expected_findings": sorted(expected), "line_accounts": accounts, "total": str(inv.payable),
            "note": note,
        }
        if spec is not None:
            issued.append((inv, spec, sef_id))
        return sef_id

    # ---- goods suppliers: PO -> GR -> invoice, with injected problems
    goods_specs = [s for s in SUPPLIERS if s.needs_po]
    po_no = 0
    for _ in range(n_po):
        spec = rng.choice(goods_specs)
        sup = suppliers[spec.key]
        po_no += 1
        order_date = START + timedelta(days=rng.randint(0, (TODAY - START).days - 6))
        po = PurchaseOrder(number=f"PO-2026-{po_no:04d}", supplier_id=sup.id, order_date=order_date,
                           cost_center=rng.choice(COST_CENTERS))
        chosen = rng.sample(spec.items, k=rng.randint(1, min(3, len(spec.items))))
        for code, _n, _u, _v, _a, price in chosen:
            po.lines.append(POLine(item_code=code, qty=Decimal(rng.choice([5, 10, 12, 20, 24, 50, 100])),
                                   unit_price=price))
        session.add(po)
        session.flush()

        # goods receipt: usually full, sometimes partial
        partial = rng.random() < 0.10
        gr = GoodsReceipt(number=f"PRI-2026-{po_no:04d}", po_id=po.id,
                          receipt_date=order_date + timedelta(days=rng.randint(1, 4)))
        for ln in po.lines:
            q = ln.qty
            if partial and ln is po.lines[0]:
                q = (ln.qty * Decimal("0.6")).quantize(Decimal("1"))
            gr.lines.append(GRLine(item_code=ln.item_code, qty=q))
        session.add(gr)
        session.flush()

        issue = gr.receipt_date + timedelta(days=rng.randint(0, 3))
        if issue > TODAY:
            continue
        expected: set[str] = set()
        note = []
        lines: list[UblLine] = []
        accounts: list[str] = []
        price_bump = rng.random()
        item_by_code = {c: (c, n, u, v, a, p) for c, n, u, v, a, p in spec.items}
        for i, pol in enumerate(po.lines, start=1):
            code, name, unit, vat, acc, price = item_by_code[pol.item_code]
            qty = pol.qty  # supplier invoices what was ordered
            unit_price = pol.unit_price
            if i == 1 and price_bump < 0.09:
                unit_price = q2(unit_price * Decimal(rng.choice(["1.04", "1.07", "1.12", "0.95"])))
                expected.add("PRICE_MISMATCH")
                note.append("price changed vs PO")
            elif i == 1 and price_bump < 0.14:
                unit_price = q2(unit_price * Decimal("1.004"))
                if unit_price != pol.unit_price:
                    expected.add("PRICE_WITHIN_TOLERANCE")
                    note.append("price +0.4% (within tolerance)")
            lines.append(UblLine(i, name, qty, unit_price, Decimal(vat), unit, code))
            accounts.append(acc)
        if partial:
            expected.add("QTY_NOT_RECEIVED")
            note.append("only 60% of first line received")
        if rng.random() < 0.03:
            extra = rng.choice([it for it in spec.items if it[0] not in {pl.item_code for pl in po.lines}] or spec.items)
            lines.append(UblLine(len(lines) + 1, extra[1], Decimal(10), extra[5], Decimal(extra[3]), extra[2], extra[0]))
            accounts.append(extra[4])
            expected.add("ITEM_NOT_ON_PO")
            note.append("line not ordered")

        inv = UblInvoice(
            number=next_number(spec, issue), issue_date=issue, due_date=issue + timedelta(days=spec.terms),
            supplier=party(spec), customer=buyer, lines=lines, order_reference=po.number,
            bank_account=sup.bank_account,
        )
        r = rng.random()
        if r < 0.03:
            inv.order_reference = None
            expected.add("NO_PO")
            note.append("PO reference missing")
        elif r < 0.06:
            inv.bank_account = make_account(rng, rng.choice(BANKS))
            expected.add("BANK_ACCOUNT_CHANGED")
            note.append("bank account differs from master data")
        elif r < 0.08:
            inv.vat_override = inv.vat_total + Decimal(rng.choice([150, 900, -400]))
            expected.update({"VAT_MISMATCH"})
            note.append("VAT total wrong")
        emit(inv, spec, expected, accounts, issue + timedelta(days=rng.randint(0, 1)), "; ".join(note))

    # ---- service suppliers: monthly invoices, no PO, free-text lines
    for spec in [s for s in SUPPLIERS if not s.needs_po]:
        sup = suppliers[spec.key]
        month = date(2026, 9, 1)
        while month <= TODAY:
            k = rng.randint(1, min(2, len(spec.service_lines)))
            picks = rng.sample(list(spec.service_lines), k=k)
            issue = month + timedelta(days=rng.randint(0, 9))
            if issue > TODAY:
                break
            lines, accounts = [], []
            for i, (desc, acc, price, vat) in enumerate(picks, start=1):
                amount = q2(price * Decimal(rng.uniform(0.85, 1.15)))
                lines.append(UblLine(i, desc, Decimal(1), amount, Decimal(vat), "C62", None))
                accounts.append(acc)
            inv = UblInvoice(
                number=next_number(spec, issue), issue_date=issue, due_date=issue + timedelta(days=spec.terms),
                supplier=party(spec), customer=buyer, lines=lines, bank_account=sup.bank_account,
            )
            expected: set[str] = set()
            note = ""
            if rng.random() < 0.06:
                inv.bank_account = make_account(rng, rng.choice(BANKS))
                expected.add("BANK_ACCOUNT_CHANGED")
                note = "bank account differs from master data"
            emit(inv, spec, expected, accounts, issue, note)
            month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)

    # ---- duplicates: same invoice re-sent (exact) or re-numbered (possible duplicate)
    originals = rng.sample(issued, k=min(len(issued), 12))
    for idx, (orig, spec, orig_id) in enumerate(originals):
        sent = min(TODAY, orig.issue_date + timedelta(days=rng.randint(3, 20)))
        accounts = truth[str(orig_id)]["line_accounts"]
        if idx % 2 == 0:
            # exact duplicate: blocked immediately, no further matching. Every second one is
            # re-typed with different separators / leading zeros, as happens with manual re-sends.
            if idx % 4 == 0:
                orig = UblInvoice(**{**orig.__dict__, "number": orig.number.replace("-", "/").replace("/0", "/")})
            emit(orig, None, {"DUPLICATE"}, accounts, sent, f"exact re-send of SEF {orig_id}")
        else:
            copy = UblInvoice(**{**orig.__dict__, "number": orig.number + "-A"})
            exp = {"POSSIBLE_DUPLICATE"} | set(truth[str(orig_id)]["expected_findings"])
            if orig.order_reference:
                exp.add("QTY_NOT_RECEIVED")  # the original already used up the received quantity
            emit(copy, None, exp, accounts, sent, f"same amount re-numbered copy of SEF {orig_id}")

    # ---- unknown supplier with an invalid PIB (classic fake-invoice pattern)
    for n in range(3):
        bad_pib = make_pib(rng)
        bad_pib = bad_pib[:8] + str((int(bad_pib[8]) + 3) % 10)
        issue = TODAY - timedelta(days=rng.randint(2, 40))
        fake = UblInvoice(
            number=f"F-{issue.year}-{900 + n}", issue_date=issue, due_date=issue + timedelta(days=3),
            supplier=UblParty(rng.choice(["Global Trade Partner d.o.o.", "Optima Servis Group d.o.o.",
                                          "Brzi Konsalting Plus d.o.o."]), bad_pib, "29999999", "Beograd"),
            customer=buyer,
            lines=[UblLine(1, rng.choice(["Konsultantske usluge po ugovoru", "Usluge optimizacije poslovanja",
                                          "Marketinske usluge - kampanja"]), Decimal(1),
                           Decimal(rng.choice([118000, 236000, 89500])), Decimal(20), "C62", None)],
            bank_account=make_account(rng, "325"),
        )
        emit(fake, None, {"UNKNOWN_SUPPLIER", "INVALID_PIB"}, ["5500"], issue, "unknown supplier, invalid PIB")

    (inbox / "_index.json").write_text(json.dumps(sent_index, indent=1), encoding="utf-8")

    # ---- bank statement: older invoices are paid, recent ones open
    for sid, t in truth.items():
        if t["expected_findings"]:
            continue  # problem invoices are not paid yet
        issue = date.fromisoformat(sent_index[sid]["sent"])
        age = (TODAY - issue).days
        if age > 35 or (age > 15 and rng.random() < 0.5):
            statement.append({
                "date": (issue + timedelta(days=rng.randint(10, 30))).isoformat(),
                "payee_pib": t["supplier_pib"], "reference": t["number"], "amount": t["total"],
            })
    statement = [p for p in statement if p["date"] <= TODAY.isoformat()]
    (out_dir / "bank_statement.json").write_text(json.dumps(statement, indent=1), encoding="utf-8")
    (out_dir / "ground_truth.json").write_text(json.dumps(truth, indent=1, ensure_ascii=False), encoding="utf-8")

    counts: dict[str, int] = {}
    for t in truth.values():
        for f in t["expected_findings"] or ["CLEAN"]:
            counts[f] = counts.get(f, 0) + 1
    return {"invoices": len(truth), "payments": len(statement), "by_type": dict(sorted(counts.items()))}
