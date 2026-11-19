export type FindingBrief = { type: string; severity: "BLOCK" | "WARN" | "INFO" };
export type InvoiceRow = {
  id: number; sef_id: number; number: string; supplier: string; issue_date: string;
  due_date: string | null; total: string; status: string; findings: FindingBrief[];
};
export type Summary = {
  as_of: string; by_status: Record<string, number>; findings_by_type: Record<string, number>;
  pending_actions: number; open_payables: string; open_in_exception: string; due_this_month: string;
  llm_enabled: boolean; llm_cost_usd: number;
};
export type Action = {
  id: number; action: string; reason: string; proposed_by: string; status: string;
  decided_by: string | null; invoice: InvoiceRow; confidence: number | null;
};
export type Entry = { account: string; name: string; debit: string; credit: string; cost_center: string | null };
export type InvoiceDetail = {
  id: number; number: string; sef_id: number; supplier: string; pib: string; issue_date: string;
  due_date: string | null; order_reference: string | null; net: string; vat: string; total: string; paid: string;
  status: string; bank_account: string | null; master_bank_account: string | null;
  lines: { no: number; item_code: string | null; description: string; qty: string; unit_price: string; net: string }[];
  findings: { type: string; severity: string; message: string }[];
  booking_proposal: { entries: Entry[]; confidence: number; rationale: string } | null;
  actions: { id: number; action: string; status: string; decided_by: string | null }[];
  history: { ts: string; actor: string; event: string; data: Record<string, unknown> }[];
};
export type AuditRow = { id: number; ts: string; actor: string; event: string; entity: string; entity_id: string; data: Record<string, unknown>; hash: string };
export type Roi = { auto_share: number; hours_saved: number; net_rsd: number; table: [string, string][] };
export type ChatReply = { answer: string; tool_calls: { name: string; input: unknown }[]; cost_usd: number; latency_ms: number };

let user = (() => { try { return localStorage.getItem("sef.user") || "ana.petrovic"; } catch { return "ana.petrovic"; } })();
export const getUser = () => user;
export const setUser = (u: string) => { user = u; try { localStorage.setItem("sef.user", u); } catch { /* ignore */ } };

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { "Content-Type": "application/json", "X-User": user, ...(init?.headers || {}) } });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

export const api = {
  summary: () => req<Summary>("/api/summary"),
  invoices: (status?: string) => req<InvoiceRow[]>(`/api/invoices${status ? `?status=${status}` : ""}`),
  invoice: (id: number) => req<InvoiceDetail>(`/api/invoices/${id}`),
  actions: () => req<Action[]>("/api/actions"),
  decide: (id: number, approve: boolean, comment: string) =>
    req(`/api/actions/${id}/decide`, { method: "POST", body: JSON.stringify({ approve, comment }) }),
  sync: () => req<{ new: number }>("/api/sync", { method: "POST" }),
  chat: (question: string) => req<ChatReply>("/api/chat", { method: "POST", body: JSON.stringify({ question }) }),
  audit: () => req<AuditRow[]>("/api/audit?limit=300"),
  verify: () => req<{ ok: boolean; first_bad_id: number | null }>("/api/audit/verify"),
  roi: (n: number, minutes: number, rate: number) =>
    req<Roi>(`/api/roi?invoices_per_month=${n}&manual_minutes_per_invoice=${minutes}&accountant_cost_rsd_per_hour=${rate}`),
};

export const rsd = (v: string | number) =>
  new Intl.NumberFormat("sr-Latn-RS", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(v)) + " RSD";
export const dt = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("sr-Latn-RS") : "-");

export const FINDING_LABEL: Record<string, string> = {
  DUPLICATE: "Duplikat", POSSIBLE_DUPLICATE: "Moguć duplikat", UNKNOWN_SUPPLIER: "Nepoznat dobavljač",
  INVALID_PIB: "Neispravan PIB", BANK_ACCOUNT_CHANGED: "Promenjen tekući račun", NO_PO: "Bez narudžbenice",
  PO_SUPPLIER_MISMATCH: "Narudžbenica drugog dobavljača", PRICE_MISMATCH: "Razlika u ceni",
  PRICE_WITHIN_TOLERANCE: "Cena u toleranciji", QTY_NOT_RECEIVED: "Količina nije primljena",
  ITEM_NOT_ON_PO: "Stavka van narudžbenice", VAT_MISMATCH: "Greška u PDV-u", TOTAL_MISMATCH: "Greška u zbiru",
};
export const STATUS_LABEL: Record<string, string> = {
  NEW: "Nova", READY: "Spremna", EXCEPTION: "Izuzetak", APPROVED: "Odobrena", REJECTED: "Odbijena",
};
