"""SEF (Sistem e-faktura) clients.

`HttpSefClient` follows the SEF public API for purchase (incoming) invoices:
  POST /api/publicApi/purchase-invoice/ids?dateFrom=&dateTo=      -> {"PurchaseInvoiceIds": [...]}
  GET  /api/publicApi/purchase-invoice/xml?invoiceId=             -> UBL XML
  POST /api/publicApi/purchase-invoice/acceptRejectPurchaseInvoice -> {invoiceId, accepted, comment}
Authentication is the `ApiKey` header (key generated in the SEF web app, per company).
Endpoint names were written from the published API documentation; verify them against
the Swagger page of your SEF environment before running against it.

`MockSefClient` serves generated UBL files from a folder with the same interface,
so the whole pipeline runs and is testable without a SEF account.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Protocol

import httpx


class SefClient(Protocol):
    def list_incoming_ids(self, date_from: date, date_to: date) -> list[int]: ...
    def get_invoice_xml(self, sef_id: int) -> str: ...
    def accept_reject(self, sef_id: int, accepted: bool, comment: str) -> dict: ...


class HttpSefClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        if not api_key:
            raise ValueError("SEF_API_KEY is required for SEF_MODE=http")
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"ApiKey": api_key, "Accept": "application/json"},
            timeout=timeout,
        )

    def list_incoming_ids(self, date_from: date, date_to: date) -> list[int]:
        r = self._http.post(
            "/api/publicApi/purchase-invoice/ids",
            params={"dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat()},
        )
        r.raise_for_status()
        body = r.json()
        return [int(x) for x in body.get("PurchaseInvoiceIds", [])]

    def get_invoice_xml(self, sef_id: int) -> str:
        r = self._http.get("/api/publicApi/purchase-invoice/xml", params={"invoiceId": sef_id},
                           headers={"Accept": "application/xml"})
        r.raise_for_status()
        return r.text

    def accept_reject(self, sef_id: int, accepted: bool, comment: str) -> dict:
        r = self._http.post(
            "/api/publicApi/purchase-invoice/acceptRejectPurchaseInvoice",
            json={"invoiceId": sef_id, "accepted": accepted, "comment": comment},
        )
        r.raise_for_status()
        return r.json() if r.content else {"invoiceId": sef_id, "accepted": accepted}


class MockSefClient:
    """Folder-backed SEF: <dir>/<sef_id>.xml plus <dir>/_index.json with dates and status."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self._index_path = self.folder / "_index.json"

    def _index(self) -> dict:
        if not self._index_path.exists():
            return {}
        return json.loads(self._index_path.read_text(encoding="utf-8"))

    def _save(self, idx: dict) -> None:
        self._index_path.write_text(json.dumps(idx, indent=1, ensure_ascii=False), encoding="utf-8")

    def list_incoming_ids(self, date_from: date, date_to: date) -> list[int]:
        out = []
        for sid, meta in self._index().items():
            d = date.fromisoformat(meta["sent"])
            if date_from <= d <= date_to:
                out.append(int(sid))
        return sorted(out)

    def get_invoice_xml(self, sef_id: int) -> str:
        return (self.folder / f"{sef_id}.xml").read_text(encoding="utf-8")

    def accept_reject(self, sef_id: int, accepted: bool, comment: str) -> dict:
        idx = self._index()
        meta = idx.setdefault(str(sef_id), {"sent": date.today().isoformat()})
        meta["status"] = "Approved" if accepted else "Rejected"
        meta["comment"] = comment
        self._save(idx)
        return {"invoiceId": sef_id, "status": meta["status"]}


def make_client() -> SefClient:
    from ..config import settings

    if settings.sef_mode == "http":
        return HttpSefClient(settings.sef_base_url, settings.sef_api_key)
    return MockSefClient(settings.mock_sef_dir)
