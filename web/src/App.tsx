import { useCallback, useEffect, useState } from "react";
import {
  Action, AuditRow, ChatReply, FINDING_LABEL, InvoiceDetail, InvoiceRow, Roi, STATUS_LABEL, Summary,
  api, dt, getUser, rsd, setUser,
} from "./api";

type Tab = "queue" | "invoices" | "chat" | "audit" | "roi";
const TABS: [Tab, string][] = [
  ["queue", "Za odobrenje"], ["invoices", "Fakture"], ["chat", "Asistent"], ["audit", "Audit log"], ["roi", "Isplativost"],
];

export default function App() {
  const [tab, setTab] = useState<Tab>("queue");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [me, setMe] = useState(getUser());
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => { api.summary().then(setSummary).catch(() => setSummary(null)); }, [tick]);

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <span className="logo">e·F</span>
          <div>
            <h1>SEF AI Agent</h1>
            <p>Ulazne e-fakture: uparivanje, kontrole, predlog knjiženja. Čovek odobrava svaku akciju.</p>
          </div>
        </div>
        <label className="user">
          Prijavljeni korisnik
          <input value={me} onChange={(e) => { setMe(e.target.value); setUser(e.target.value); }} />
        </label>
      </header>

      {summary && <Kpis s={summary} onSync={async () => { await api.sync(); refresh(); }} />}

      <nav className="tabs" role="tablist">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
            {label}{k === "queue" && summary ? <span className="pill">{summary.pending_actions}</span> : null}
          </button>
        ))}
      </nav>

      <main>
        {tab === "queue" && <Queue key={tick} onOpen={setOpenId} onChange={refresh} />}
        {tab === "invoices" && <Invoices key={tick} onOpen={setOpenId} />}
        {tab === "chat" && <Chat llm={summary?.llm_enabled ?? false} />}
        {tab === "audit" && <Audit key={tick} />}
        {tab === "roi" && <RoiView />}
      </main>

      {openId !== null && <Drawer id={openId} onClose={() => setOpenId(null)} onChange={refresh} />}
      <footer>Demo podaci su sintetički. Vlasnik projekta i orkestracija: Lazar Mihajlović · Implementacija: Claude (AI).</footer>
    </div>
  );
}

function Kpis({ s, onSync }: { s: Summary; onSync: () => void }) {
  const total = Object.values(s.by_status).reduce((a, b) => a + b, 0);
  const exc = s.by_status.EXCEPTION ?? 0;
  return (
    <section className="kpis">
      <Kpi label="Faktura u sistemu" value={String(total)} sub={`stanje na ${dt(s.as_of)}`} />
      <Kpi label="Izuzeci za proveru" value={String(exc)} sub={`${total ? Math.round((100 * exc) / total) : 0}% faktura`} tone="warn" />
      <Kpi label="Dospeva do kraja meseca" value={rsd(s.due_this_month)} sub="neplaćeno" />
      <Kpi label="Ukupno otvoreno" value={rsd(s.open_payables)} sub={`od toga sporno ${rsd(s.open_in_exception)}`} />
      <div className="kpi actions">
        <button className="primary" onClick={onSync}>Preuzmi nove iz SEF-a</button>
        <small>{s.llm_enabled ? `LLM uključen · potrošeno $${s.llm_cost_usd.toFixed(4)}` : "LLM isključen (radi samo sa pravilima)"}</small>
      </div>
    </section>
  );
}

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub: string; tone?: string }) {
  return (
    <div className={`kpi ${tone ?? ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{sub}</small>
    </div>
  );
}

function Badges({ f }: { f: { type: string; severity: string }[] }) {
  if (!f.length) return <span className="badge ok">bez odstupanja</span>;
  return <>{f.map((x, i) => <span key={i} className={`badge ${x.severity.toLowerCase()}`}>{FINDING_LABEL[x.type] ?? x.type}</span>)}</>;
}

function Queue({ onOpen, onChange }: { onOpen: (id: number) => void; onChange: () => void }) {
  const [rows, setRows] = useState<Action[] | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.actions().then(setRows).catch((e) => setErr(String(e))); }, []);
  const act = async (a: Action, approve: boolean) => {
    setBusy(a.id); setErr("");
    try { await api.decide(a.id, approve, approve ? "Odobreno" : "Odbijeno u pregledu"); onChange(); }
    catch (e) { setErr(String(e)); } finally { setBusy(null); }
  };
  if (!rows) return <p className="muted">Učitavanje…</p>;
  return (
    <section>
      <p className="lead">Agent je pripremio predloge. Ništa se ne knjiži i ne šalje u SEF dok ga ne odobri imenovana osoba.</p>
      {err && <p className="error">{err}</p>}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Predlog</th><th>Faktura</th><th>Dobavljač</th><th className="num">Iznos</th><th>Nalazi</th><th>Sigurnost</th><th /></tr></thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.id}>
                <td><span className={`tag ${a.action === "REJECT_IN_SEF" ? "danger" : ""}`}>{a.action === "APPROVE_BOOKING" ? "Proknjiži i prihvati" : "Odbij u SEF-u"}</span></td>
                <td><button className="link" onClick={() => onOpen(a.invoice.id)}>{a.invoice.number}</button></td>
                <td>{a.invoice.supplier}</td>
                <td className="num">{rsd(a.invoice.total)}</td>
                <td><Badges f={a.invoice.findings} /></td>
                <td>{a.confidence != null ? `${Math.round(a.confidence * 100)}%` : "-"}</td>
                <td className="row-actions">
                  <button disabled={busy === a.id} className="primary" onClick={() => act(a, true)}>Odobri</button>
                  <button disabled={busy === a.id} onClick={() => act(a, false)}>Odbaci</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length && <p className="muted">Nema predloga koji čekaju odobrenje.</p>}
    </section>
  );
}

function Invoices({ onOpen }: { onOpen: (id: number) => void }) {
  const [status, setStatus] = useState("EXCEPTION");
  const [rows, setRows] = useState<InvoiceRow[]>([]);
  useEffect(() => { api.invoices(status || undefined).then(setRows); }, [status]);
  return (
    <section>
      <div className="filters">
        {["EXCEPTION", "READY", "APPROVED", "REJECTED", ""].map((s) => (
          <button key={s} className={status === s ? "on" : ""} onClick={() => setStatus(s)}>{s ? STATUS_LABEL[s] : "Sve"}</button>
        ))}
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Broj</th><th>Dobavljač</th><th>Datum</th><th>Dospeće</th><th className="num">Iznos</th><th>Status</th><th>Nalazi</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} onClick={() => onOpen(r.id)} className="clickable">
                <td className="nowrap">{r.number}</td><td>{r.supplier}</td><td className="nowrap">{dt(r.issue_date)}</td><td className="nowrap">{dt(r.due_date)}</td>
                <td className="num">{rsd(r.total)}</td><td><span className={`status ${r.status.toLowerCase()}`}>{STATUS_LABEL[r.status]}</span></td>
                <td><Badges f={r.findings} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Drawer({ id, onClose, onChange }: { id: number; onClose: () => void; onChange: () => void }) {
  const [d, setD] = useState<InvoiceDetail | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.invoice(id).then(setD); }, [id]);
  const pending = d?.actions.find((a) => a.status === "PENDING");
  const decide = async (approve: boolean) => {
    if (!pending) return;
    try { await api.decide(pending.id, approve, approve ? "Odobreno iz detalja" : "Odbijeno iz detalja"); onChange(); onClose(); }
    catch (e) { setErr(String(e)); }
  };
  return (
    <div className="overlay" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()} aria-label="Detalji fakture">
        <button className="close" onClick={onClose} aria-label="Zatvori">×</button>
        {!d ? <p className="muted">Učitavanje…</p> : (
          <>
            <h2>{d.number}</h2>
            <p className="muted">{d.supplier} · PIB {d.pib} · SEF ID {d.sef_id}</p>
            <dl className="facts">
              <div><dt>Datum</dt><dd>{dt(d.issue_date)}</dd></div>
              <div><dt>Dospeće</dt><dd>{dt(d.due_date)}</dd></div>
              <div><dt>Narudžbenica</dt><dd>{d.order_reference ?? "-"}</dd></div>
              <div><dt>Osnovica</dt><dd>{rsd(d.net)}</dd></div>
              <div><dt>PDV</dt><dd>{rsd(d.vat)}</dd></div>
              <div><dt>Ukupno</dt><dd><b>{rsd(d.total)}</b></dd></div>
              <div><dt>Plaćeno</dt><dd>{rsd(d.paid)}</dd></div>
              <div><dt>Status</dt><dd>{STATUS_LABEL[d.status]}</dd></div>
            </dl>

            <h3>Nalazi kontrola</h3>
            {d.findings.length ? (
              <ul className="findings">
                {d.findings.map((f, i) => <li key={i} className={f.severity.toLowerCase()}><b>{FINDING_LABEL[f.type] ?? f.type}.</b> {f.message}</li>)}
              </ul>
            ) : <p className="ok-text">Faktura se slaže sa narudžbenicom i prijemnicom.</p>}

            <h3>Stavke</h3>
            <div className="table-wrap"><table>
              <thead><tr><th>#</th><th>Opis</th><th className="num">Kol.</th><th className="num">Cena</th><th className="num">Iznos</th></tr></thead>
              <tbody>{d.lines.map((l) => <tr key={l.no}><td>{l.no}</td><td>{l.description}<br /><small className="muted">{l.item_code ?? "slobodan tekst"}</small></td><td className="num">{Number(l.qty)}</td><td className="num">{rsd(l.unit_price)}</td><td className="num">{rsd(l.net)}</td></tr>)}</tbody>
            </table></div>

            {d.booking_proposal && (
              <>
                <h3>Predlog knjiženja <small className="muted">sigurnost {Math.round(d.booking_proposal.confidence * 100)}%</small></h3>
                <div className="table-wrap"><table>
                  <thead><tr><th>Konto</th><th>Naziv</th><th className="num">Duguje</th><th className="num">Potražuje</th></tr></thead>
                  <tbody>{d.booking_proposal.entries.map((e, i) => <tr key={i}><td>{e.account}</td><td>{e.name}{e.cost_center ? <small className="muted"> · {e.cost_center}</small> : null}</td><td className="num">{Number(e.debit) ? rsd(e.debit) : ""}</td><td className="num">{Number(e.credit) ? rsd(e.credit) : ""}</td></tr>)}</tbody>
                </table></div>
                <p className="muted small">{d.booking_proposal.rationale}</p>
              </>
            )}

            {pending && (
              <div className="decide">
                <span>Predlog: <b>{pending.action === "APPROVE_BOOKING" ? "proknjiži i prihvati u SEF-u" : "odbij u SEF-u"}</b></span>
                <button className="primary" onClick={() => decide(true)}>Odobri</button>
                <button onClick={() => decide(false)}>Odbaci</button>
              </div>
            )}
            {err && <p className="error">{err}</p>}

            <h3>Istorija</h3>
            <ol className="history">{d.history.map((h, i) => <li key={i}><time>{new Date(h.ts).toLocaleString("sr-Latn-RS")}</time> <b>{h.actor}</b> {h.event}</li>)}</ol>
          </>
        )}
      </aside>
    </div>
  );
}

const EXAMPLES = [
  "Koliko dugujemo dobavljaču Agro Lux do kraja meseca?",
  "Koje fakture imaju razliku u ceni u odnosu na narudžbenicu?",
  "Kod kog dobavljača smo imali najveći trošak u oktobru?",
  "Koliko je ukupno dospelo a nije plaćeno?",
];

function Chat({ llm }: { llm: boolean }) {
  const [q, setQ] = useState("");
  const [log, setLog] = useState<{ q: string; r?: ChatReply; err?: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const send = async (text: string) => {
    if (!text.trim()) return;
    setBusy(true); setQ("");
    const i = log.length;
    setLog((l) => [...l, { q: text }]);
    try { const r = await api.chat(text); setLog((l) => l.map((x, j) => (j === i ? { ...x, r } : x))); }
    catch (e) { setLog((l) => l.map((x, j) => (j === i ? { ...x, err: String(e) } : x))); }
    finally { setBusy(false); }
  };
  return (
    <section className="chat">
      {!llm && <p className="notice">LLM nije uključen na ovoj instanci (nema API ključa). Pravila, kontrole i odobravanje rade; asistent odgovara kada se ključ podesi.</p>}
      <div className="examples">{EXAMPLES.map((e) => <button key={e} onClick={() => send(e)} disabled={busy}>{e}</button>)}</div>
      <div className="msgs">
        {log.map((m, i) => (
          <div key={i} className="turn">
            <p className="q">{m.q}</p>
            {m.r ? (
              <div className="a">
                <p>{m.r.answer}</p>
                <small className="muted">alati: {m.r.tool_calls.map((t) => t.name).join(", ") || "-"} · ${m.r.cost_usd.toFixed(4)} · {(m.r.latency_ms / 1000).toFixed(1)} s</small>
              </div>
            ) : m.err ? <p className="error">{m.err}</p> : <p className="muted">Agent radi…</p>}
          </div>
        ))}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); send(q); }} className="ask">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Pitajte nešto o ulaznim fakturama…" />
        <button className="primary" disabled={busy}>Pošalji</button>
      </form>
    </section>
  );
}

function Audit() {
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [v, setV] = useState<{ ok: boolean; first_bad_id: number | null } | null>(null);
  useEffect(() => { api.audit().then(setRows); api.verify().then(setV); }, []);
  return (
    <section>
      <p className="lead">
        Svaki događaj čuva heš prethodnog, pa se izmena ili brisanje istorije odmah otkriva.{" "}
        {v && (v.ok ? <span className="badge ok">lanac ispravan</span> : <span className="badge block">lanac prekinut kod #{v.first_bad_id}</span>)}
      </p>
      <div className="table-wrap"><table>
        <thead><tr><th>#</th><th>Vreme</th><th>Ko</th><th>Događaj</th><th>Objekat</th><th>Heš</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.id}><td>{r.id}</td><td>{new Date(r.ts).toLocaleString("sr-Latn-RS")}</td><td>{r.actor}</td><td>{r.event}</td><td>{r.entity} {r.entity_id}</td><td><code>{r.hash}</code></td></tr>)}</tbody>
      </table></div>
    </section>
  );
}

function RoiView() {
  const [n, setN] = useState(400);
  const [m, setM] = useState(12);
  const [rate, setRate] = useState(1250);
  const [r, setR] = useState<Roi | null>(null);
  useEffect(() => { const t = setTimeout(() => api.roi(n, m, rate).then(setR), 200); return () => clearTimeout(t); }, [n, m, rate]);
  return (
    <section className="roi">
      <div className="sliders">
        <label>Faktura mesečno <b>{n}</b><input type="range" min={50} max={3000} step={50} value={n} onChange={(e) => setN(+e.target.value)} /></label>
        <label>Minuta po fakturi ručno <b>{m}</b><input type="range" min={3} max={30} value={m} onChange={(e) => setM(+e.target.value)} /></label>
        <label>Trošak sata knjigovođe <b>{rate} RSD</b><input type="range" min={600} max={3000} step={50} value={rate} onChange={(e) => setRate(+e.target.value)} /></label>
      </div>
      {r && (
        <div className="roi-out">
          <div className="big"><span>Neto ušteda mesečno</span><strong>{rsd(r.net_rsd)}</strong><small>{Math.round(r.hours_saved)} sati rada manje</small></div>
          <table><tbody>{r.table.map(([k, v]) => <tr key={k}><td>{k}</td><td className="num">{v}</td></tr>)}</tbody></table>
          <p className="muted small">Udeo faktura spremnih za odobrenje jednim klikom je izmeren na demo podacima; ostalo su pretpostavke koje se menjaju po klijentu.</p>
        </div>
      )}
    </section>
  );
}
