"""Build and parse UBL 2.1 invoices in the Serbian SEF profile (EN 16931 + SRBDT)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from lxml import etree

NS = {
    "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
CUSTOMIZATION_ID = "urn:cen.eu:en16931:2017#compliant#urn:mfin.gov.rs:srbdt:2022"
TWO = Decimal("0.01")


def q2(x: Decimal) -> Decimal:
    return Decimal(x).quantize(TWO, rounding=ROUND_HALF_UP)


@dataclass
class UblParty:
    name: str
    pib: str
    mb: str
    city: str = ""
    street: str = ""


@dataclass
class UblLine:
    line_no: int
    description: str
    qty: Decimal
    unit_price: Decimal
    vat_rate: Decimal
    unit: str = "H87"  # UN/ECE rec 20: piece
    item_code: str | None = None

    @property
    def net(self) -> Decimal:
        return q2(self.qty * self.unit_price)


@dataclass
class UblInvoice:
    number: str
    issue_date: date
    due_date: date | None
    supplier: UblParty
    customer: UblParty
    lines: list[UblLine]
    order_reference: str | None = None
    bank_account: str | None = None
    currency: str = "RSD"
    # Optional overrides, used by the generator to inject arithmetic errors
    vat_override: Decimal | None = None
    total_override: Decimal | None = None
    note: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def net_total(self) -> Decimal:
        return q2(sum((ln.net for ln in self.lines), Decimal("0")))

    def vat_breakdown(self) -> dict[Decimal, tuple[Decimal, Decimal]]:
        groups: dict[Decimal, Decimal] = {}
        for ln in self.lines:
            groups[ln.vat_rate] = groups.get(ln.vat_rate, Decimal("0")) + ln.net
        return {rate: (q2(base), q2(base * rate / 100)) for rate, base in groups.items()}

    @property
    def vat_total(self) -> Decimal:
        if self.vat_override is not None:
            return q2(self.vat_override)
        return q2(sum((v for _, v in self.vat_breakdown().values()), Decimal("0")))

    @property
    def payable(self) -> Decimal:
        if self.total_override is not None:
            return q2(self.total_override)
        return q2(self.net_total + self.vat_total)


# ------------------------------------------------------------------ building
def _sub(parent, tag: str, text=None, **attrib):
    prefix, local = tag.split(":")
    el = etree.SubElement(parent, f"{{{NS[prefix]}}}{local}", **attrib)
    if text is not None:
        el.text = str(text)
    return el


def _party(parent, tag: str, p: UblParty):
    wrap = _sub(parent, tag)
    party = _sub(wrap, "cac:Party")
    _sub(party, "cbc:EndpointID", p.pib, schemeID="9948")
    _sub(_sub(party, "cac:PartyName"), "cbc:Name", p.name)
    addr = _sub(party, "cac:PostalAddress")
    _sub(addr, "cbc:StreetName", p.street or "-")
    _sub(addr, "cbc:CityName", p.city or "-")
    _sub(_sub(addr, "cac:Country"), "cbc:IdentificationCode", "RS")
    pts = _sub(party, "cac:PartyTaxScheme")
    _sub(pts, "cbc:CompanyID", f"RS{p.pib}")
    _sub(_sub(pts, "cac:TaxScheme"), "cbc:ID", "VAT")
    ple = _sub(party, "cac:PartyLegalEntity")
    _sub(ple, "cbc:RegistrationName", p.name)
    _sub(ple, "cbc:CompanyID", p.mb)


def build_invoice_xml(inv: UblInvoice) -> str:
    root = etree.Element(f"{{{NS['inv']}}}Invoice", nsmap={None: NS["inv"], "cac": NS["cac"], "cbc": NS["cbc"]})
    _sub(root, "cbc:CustomizationID", CUSTOMIZATION_ID)
    _sub(root, "cbc:ID", inv.number)
    _sub(root, "cbc:IssueDate", inv.issue_date.isoformat())
    if inv.due_date:
        _sub(root, "cbc:DueDate", inv.due_date.isoformat())
    _sub(root, "cbc:InvoiceTypeCode", "380")
    if inv.note:
        _sub(root, "cbc:Note", inv.note)
    _sub(root, "cbc:DocumentCurrencyCode", inv.currency)
    _sub(_sub(root, "cac:InvoicePeriod"), "cbc:DescriptionCode", "35")  # VAT point: delivery date
    if inv.order_reference:
        _sub(_sub(root, "cac:OrderReference"), "cbc:ID", inv.order_reference)
    _party(root, "cac:AccountingSupplierParty", inv.supplier)
    _party(root, "cac:AccountingCustomerParty", inv.customer)
    if inv.bank_account:
        pm = _sub(root, "cac:PaymentMeans")
        _sub(pm, "cbc:PaymentMeansCode", "30")
        _sub(_sub(pm, "cac:PayeeFinancialAccount"), "cbc:ID", inv.bank_account)

    cur = {"currencyID": inv.currency}
    tt = _sub(root, "cac:TaxTotal")
    _sub(tt, "cbc:TaxAmount", inv.vat_total, **cur)
    for rate, (base, vat) in sorted(inv.vat_breakdown().items()):
        st = _sub(tt, "cac:TaxSubtotal")
        _sub(st, "cbc:TaxableAmount", base, **cur)
        _sub(st, "cbc:TaxAmount", vat, **cur)
        cat = _sub(st, "cac:TaxCategory")
        _sub(cat, "cbc:ID", "S" if rate > 0 else "Z")
        _sub(cat, "cbc:Percent", rate)
        _sub(_sub(cat, "cac:TaxScheme"), "cbc:ID", "VAT")

    lmt = _sub(root, "cac:LegalMonetaryTotal")
    _sub(lmt, "cbc:LineExtensionAmount", inv.net_total, **cur)
    _sub(lmt, "cbc:TaxExclusiveAmount", inv.net_total, **cur)
    _sub(lmt, "cbc:TaxInclusiveAmount", inv.payable, **cur)
    _sub(lmt, "cbc:PayableAmount", inv.payable, **cur)

    for ln in inv.lines:
        il = _sub(root, "cac:InvoiceLine")
        _sub(il, "cbc:ID", ln.line_no)
        _sub(il, "cbc:InvoicedQuantity", ln.qty, unitCode=ln.unit)
        _sub(il, "cbc:LineExtensionAmount", ln.net, **cur)
        item = _sub(il, "cac:Item")
        _sub(item, "cbc:Name", ln.description)
        if ln.item_code:
            _sub(_sub(item, "cac:SellersItemIdentification"), "cbc:ID", ln.item_code)
        ctc = _sub(item, "cac:ClassifiedTaxCategory")
        _sub(ctc, "cbc:ID", "S" if ln.vat_rate > 0 else "Z")
        _sub(ctc, "cbc:Percent", ln.vat_rate)
        _sub(_sub(ctc, "cac:TaxScheme"), "cbc:ID", "VAT")
        _sub(_sub(il, "cac:Price"), "cbc:PriceAmount", ln.unit_price, **cur)

    return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8").decode()


# ------------------------------------------------------------------ parsing
@dataclass
class ParsedLine:
    line_no: int
    description: str
    qty: Decimal
    unit: str
    unit_price: Decimal
    vat_rate: Decimal
    net_amount: Decimal
    item_code: str | None


@dataclass
class ParsedInvoice:
    number: str
    issue_date: date
    due_date: date | None
    currency: str
    supplier_name: str
    supplier_pib: str
    supplier_mb: str | None
    customer_pib: str | None
    order_reference: str | None
    bank_account: str | None
    net_amount: Decimal
    vat_amount: Decimal
    total_amount: Decimal
    lines: list[ParsedLine]


class UblParseError(ValueError):
    pass


def _t(node, path: str) -> str | None:
    found = node.find(path, NS)
    return found.text.strip() if found is not None and found.text else None


def _d(node, path: str, default: str | None = None) -> Decimal:
    val = _t(node, path)
    if val is None:
        if default is None:
            raise UblParseError(f"missing {path}")
        val = default
    return Decimal(val)


def _strip_envelope(xml: str | bytes) -> etree._Element:
    data = xml.encode() if isinstance(xml, str) else xml
    root = etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    if root.tag == f"{{{NS['inv']}}}Invoice":
        return root
    # SEF can wrap the UBL document in an envelope; find the Invoice inside
    found = root.find(".//inv:Invoice", NS)
    if found is None:
        raise UblParseError("no UBL Invoice element found")
    return found


def parse_invoice_xml(xml: str | bytes) -> ParsedInvoice:
    root = _strip_envelope(xml)
    sup = root.find("cac:AccountingSupplierParty/cac:Party", NS)
    cus = root.find("cac:AccountingCustomerParty/cac:Party", NS)
    if sup is None:
        raise UblParseError("missing supplier party")
    pib = _t(sup, "cbc:EndpointID") or (_t(sup, "cac:PartyTaxScheme/cbc:CompanyID") or "").removeprefix("RS")
    lines = []
    for il in root.findall("cac:InvoiceLine", NS):
        qty_el = il.find("cbc:InvoicedQuantity", NS)
        lines.append(
            ParsedLine(
                line_no=int(_t(il, "cbc:ID") or 0),
                description=_t(il, "cac:Item/cbc:Name") or "",
                qty=Decimal(qty_el.text.strip()),
                unit=qty_el.get("unitCode", "H87"),
                unit_price=_d(il, "cac:Price/cbc:PriceAmount"),
                vat_rate=_d(il, "cac:Item/cac:ClassifiedTaxCategory/cbc:Percent", "0"),
                net_amount=_d(il, "cbc:LineExtensionAmount"),
                item_code=_t(il, "cac:Item/cac:SellersItemIdentification/cbc:ID"),
            )
        )
    due = _t(root, "cbc:DueDate")
    return ParsedInvoice(
        number=_t(root, "cbc:ID") or "",
        issue_date=date.fromisoformat(_t(root, "cbc:IssueDate") or ""),
        due_date=date.fromisoformat(due) if due else None,
        currency=_t(root, "cbc:DocumentCurrencyCode") or "RSD",
        supplier_name=_t(sup, "cac:PartyName/cbc:Name") or _t(sup, "cac:PartyLegalEntity/cbc:RegistrationName") or "",
        supplier_pib=pib,
        supplier_mb=_t(sup, "cac:PartyLegalEntity/cbc:CompanyID"),
        customer_pib=_t(cus, "cbc:EndpointID") if cus is not None else None,
        order_reference=_t(root, "cac:OrderReference/cbc:ID"),
        bank_account=_t(root, "cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID"),
        net_amount=_d(root, "cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount"),
        vat_amount=_d(root, "cac:TaxTotal/cbc:TaxAmount", "0"),
        total_amount=_d(root, "cac:LegalMonetaryTotal/cbc:PayableAmount"),
        lines=lines,
    )
