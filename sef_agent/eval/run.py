"""Evaluation against the generated ground truth.

Measures
  1. anomaly detection: precision / recall per finding type, routing accuracy
     (did the invoice go to a person exactly when it should?)
  2. booking: account accuracy per line, rules vs LLM, on free-text lines and overall
  3. Q&A (optional, needs API key): numeric answers checked against SQL ground truth
  4. cost and latency per document
Writes a Markdown report (docs/eval_report.md) and a JSON with raw numbers.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from .. import llm
from ..booking.classifier import LineInput, LLMClassifier, RuleClassifier
from ..db import SessionLocal
from ..matching.engine import SEVERITY
from ..models import FindingType, Invoice, Item, LLMCall, Severity

BLOCKING = {t.value for t, s in SEVERITY.items() if s == Severity.BLOCK}


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def eval_detection(session, truth: dict) -> dict:
    tp, fp, fn = Counter(), Counter(), Counter()
    routing_ok = exact_ok = 0
    errors = []
    invoices = {str(i.sef_id): i for i in session.execute(select(Invoice)).scalars()}
    for sid, t in truth.items():
        inv = invoices[sid]
        exp = set(t["expected_findings"])
        got = {f.type for f in inv.findings}
        for x in exp & got:
            tp[x] += 1
        for x in got - exp:
            fp[x] += 1
        for x in exp - got:
            fn[x] += 1
        should_block = bool(exp & BLOCKING)
        did_block = inv.status in ("EXCEPTION",)
        routing_ok += should_block == did_block
        exact_ok += exp == got
        if exp != got:
            errors.append({"sef_id": sid, "number": t["number"], "expected": sorted(exp), "got": sorted(got),
                           "note": t["note"]})
    per_type = {}
    for ft in FindingType:
        k = ft.value
        if tp[k] + fp[k] + fn[k] == 0:
            continue
        p = tp[k] / (tp[k] + fp[k]) if tp[k] + fp[k] else 1.0
        r = tp[k] / (tp[k] + fn[k]) if tp[k] + fn[k] else 1.0
        per_type[k] = {"support": tp[k] + fn[k], "tp": tp[k], "fp": fp[k], "fn": fn[k], "precision": p, "recall": r}
    n = len(truth)
    all_tp, all_fp, all_fn = sum(tp.values()), sum(fp.values()), sum(fn.values())
    return {
        "invoices": n, "routing_accuracy": routing_ok / n,
        "ready_share": sum(1 for i in invoices.values() if i.status != "EXCEPTION") / n, "exact_set_accuracy": exact_ok / n,
        "micro_precision": all_tp / (all_tp + all_fp) if all_tp + all_fp else 1.0,
        "micro_recall": all_tp / (all_tp + all_fn) if all_tp + all_fn else 1.0,
        "per_type": per_type, "errors": errors,
    }


def eval_booking(session, truth: dict, classifiers: list[str]) -> dict:
    items = {it.code for it in session.execute(select(Item)).scalars()}
    invoices = {str(i.sef_id): i for i in session.execute(select(Invoice)).scalars()}
    seen: set[tuple] = set()
    master_lines, free_lines = [], []
    for sid, t in truth.items():
        inv = invoices[sid]
        for ln, acc in zip(inv.lines, t["line_accounts"]):
            key = (inv.supplier_pib, ln.description, str(ln.net_amount))
            if key in seen:  # duplicates would double-count the same line
                continue
            seen.add(key)
            if ln.item_code in items:
                master_lines.append(acc)
            else:
                free_lines.append((LineInput(ln.description, inv.supplier_name,
                                             inv.supplier.category if inv.supplier else None,
                                             str(ln.net_amount)), acc, inv.id))
    hard = json.loads((Path(__file__).parent / "booking_hard_set.json").read_text(encoding="utf-8"))
    hard_lines = [(LineInput(h["description"], h["supplier"], h["category"], "0"), h["account"], None) for h in hard]
    results = {"master_lines": len(master_lines), "free_text_lines": len(free_lines),
               "hard_lines": len(hard_lines), "classifiers": {}}
    for name in classifiers:
        if name == "llm" and not llm.llm_available():
            results["classifiers"]["llm"] = {"skipped": "no LLM API key set"}
            continue
        clf = LLMClassifier(session=session) if name == "llm" else RuleClassifier()
        gen = _score(session, clf, name, free_lines)
        hrd = _score(session, clf, name, hard_lines)
        n = len(free_lines)
        results["classifiers"][name] = {
            "model": getattr(clf, "model", None),
            "free_text_accuracy": gen["accuracy"],
            "overall_accuracy": (gen["correct"] + len(master_lines)) / (n + len(master_lines)),
            "hard_set_accuracy": hrd["accuracy"],
            "cost_usd": gen["cost"] + hrd["cost"],
            "cost_per_line_usd": (gen["cost"] + hrd["cost"]) / (n + len(hard_lines)),
            "avg_latency_ms": (gen["ms"] + hrd["ms"]) / (n + len(hard_lines)),
            "errors": gen["errors"] + hrd["errors"],
        }
    return results


def _score(session, clf, name: str, lines: list) -> dict:
    before = _llm_totals(session)
    t0 = time.perf_counter()
    correct, wrong = 0, []
    for li, acc, inv_id in lines:
        c = clf.classify(li, invoice_id=inv_id) if name == "llm" else clf.classify(li)
        if c.account == acc:
            correct += 1
        else:
            wrong.append({"description": li.description, "expected": acc, "got": c.account,
                          "confidence": c.confidence, "reason": c.reason})
    session.flush()
    return {"correct": correct, "accuracy": correct / len(lines) if lines else 1.0,
            "cost": _llm_totals(session)["cost"] - before["cost"],
            "ms": (time.perf_counter() - t0) * 1000, "errors": wrong}


def _llm_totals(session) -> dict:
    row = session.execute(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0))).one()
    return {"cost": float(row[0])}


# ------------------------------------------------------------------ Q&A
def qa_cases(session) -> list[dict]:
    from ..agent import queries

    cases = []
    sup = session.execute(select(Invoice.supplier_pib, Invoice.supplier_name)
                          .where(Invoice.supplier_id.is_not(None)).distinct()).all()
    import random

    rng = random.Random(3)
    for pib, name in rng.sample(sup, k=min(5, len(sup))):
        r = queries.payables(session, supplier_query=name, due_until="2026-11-30")
        cases.append({"q": f"Koliko dugujemo dobavljacu {name} sa dospecem do kraja meseca?",
                      "expected": r["total_open"], "kind": "number"})
    r = queries.payables(session, due_until="2026-11-30")
    cases.append({"q": "Koliko ukupno moramo da platimo dobavljacima do 30.11.2026?", "expected": r["total_open"],
                  "kind": "number"})
    r = queries.payables(session, overdue_only=True)
    cases.append({"q": "Koliki je ukupan iznos dospelih a neplacenih faktura na danasnji dan?",
                  "expected": r["total_open"], "kind": "number"})
    n_exc = session.execute(select(func.count()).select_from(Invoice).where(Invoice.status == "EXCEPTION")).scalar()
    cases.append({"q": "Koliko faktura trenutno ceka proveru zbog odstupanja?", "expected": str(n_exc), "kind": "int"})
    top = queries.spend_by_supplier(session, date_from="2026-10-01", date_to="2026-10-31", limit=1)
    if top["rows"]:
        cases.append({"q": "Kod kog dobavljaca smo imali najveci trosak (bez PDV-a) u oktobru 2026?",
                      "expected": top["rows"][0]["supplier"], "kind": "text"})
    return cases


_NUM = re.compile(r"\d{1,3}(?:[.\s]\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?|\d+(?:\.\d{1,2})?")


def _numbers(text: str) -> list[Decimal]:
    out = []
    for m in _NUM.findall(text):
        s = m.replace(" ", "")
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        elif s.count(".") > 1 or re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")
        try:
            out.append(Decimal(s))
        except Exception:
            pass
    return out


def eval_qa(session) -> dict:
    from ..agent.runner import ask

    if not llm.llm_available():
        return {"skipped": "no LLM API key set"}
    rows, ok_n, cost = [], 0, 0.0
    for c in qa_cases(session):
        res = ask(session, c["q"], user="eval")
        if c["kind"] == "text":
            ok = c["expected"].lower() in res.answer.lower()
        else:
            exp = Decimal(c["expected"])
            ok = any(abs(n - exp) <= Decimal("1") for n in _numbers(res.answer))
        ok_n += ok
        cost += res.cost_usd
        rows.append({"question": c["q"], "expected": c["expected"], "answer": res.answer, "ok": ok,
                     "tools": [t["name"] for t in res.tool_calls], "cost_usd": res.cost_usd,
                     "latency_ms": res.latency_ms})
    return {"accuracy": ok_n / len(rows), "n": len(rows), "cost_usd": cost, "cases": rows}


# ------------------------------------------------------------------ report
def run_eval(data_dir: Path, classifiers: list[str], with_qa: bool, out: Path) -> dict:
    from ..roi import RoiInputs, compute_roi

    truth = json.loads((data_dir / "ground_truth.json").read_text(encoding="utf-8"))
    with SessionLocal() as s:
        det = eval_detection(s, truth)
        book = eval_booking(s, truth, classifiers)
        qa = eval_qa(s) if with_qa else {"skipped": "run with --qa"}
        s.commit()
    llm_book = book["classifiers"].get("llm", {})
    per_doc_llm = llm_book.get("cost_per_line_usd", 0) * (book["free_text_lines"] / max(det["invoices"], 1))
    roi = compute_roi(RoiInputs(), auto_share=det["ready_share"], llm_cost_per_invoice_usd=per_doc_llm)
    report = {"detection": det, "booking": book, "qa": qa, "roi": roi}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    out.write_text(render(report), encoding="utf-8")
    print(render(report))
    return report


def render(r: dict) -> str:
    d, b, q, roi = r["detection"], r["booking"], r["qa"], r["roi"]
    L = ["# Evaluation report", "",
         f"Dataset: {d['invoices']} synthetic SEF invoices with injected problems (see `data/ground_truth.json`).", "",
         "> **How to read this.** The data generator and the rules were written by the same team, so section 1 shows "
         "that the controls are implemented as specified, not how they would score on a real company's invoices. "
         "Real-world accuracy needs a pilot on real SEF data. Sections 2 and 3 include a hand-written hard set and "
         "SQL-checked questions to make the LLM numbers more honest.", "",
         "## 1. Anomaly detection (rules engine)", "",
         "| Metric | Value |", "|---|---|",
         f"| Routing accuracy (sent to a person exactly when needed) | {_pct(d['routing_accuracy'])} |",
         f"| Exact finding-set accuracy | {_pct(d['exact_set_accuracy'])} |",
         f"| Micro precision | {_pct(d['micro_precision'])} |",
         f"| Micro recall | {_pct(d['micro_recall'])} |", "",
         "| Finding type | Support | Precision | Recall |", "|---|---|---|---|"]
    for k, v in sorted(d["per_type"].items()):
        L.append(f"| {k} | {v['support']} | {_pct(v['precision'])} | {_pct(v['recall'])} |")
    if d["errors"]:
        L += ["", f"Mismatches ({len(d['errors'])}):", ""]
        for e in d["errors"][:15]:
            L.append(f"- SEF {e['sef_id']} {e['number']}: expected {e['expected']}, got {e['got']} ({e['note']})")
        L += ["", "False POSSIBLE_DUPLICATE flags happen when a supplier legitimately sends two invoices with the "
              "same amount within the window (same order repeated). This is the intended trade-off: a person "
              "spends one minute checking, instead of the company paying twice."]
    L += ["", "## 2. Booking proposals (account per line)", "",
          f"{b['master_lines']} lines have an item code (account from item master, deterministic); "
          f"{b['free_text_lines']} unique free-text lines need classification.", "",
          f"The hard set is {b['hard_lines']} hand-written, deliberately ambiguous service descriptions that the "
          "keyword rules were not written for (labels follow the simplified chart of accounts in this repo and "
          "were not reviewed by a certified accountant).", "",
          "| Classifier | Model | Generated free-text | Overall (all lines) | Hard set | Cost per line | Avg latency |",
          "|---|---|---|---|---|---|---|"]
    for name, v in b["classifiers"].items():
        if "skipped" in v:
            L.append(f"| {name} | - | skipped: {v['skipped']} | | | | |")
            continue
        L.append(f"| {name} | {v['model'] or '-'} | {_pct(v['free_text_accuracy'])} | {_pct(v['overall_accuracy'])} | "
                 f"{_pct(v['hard_set_accuracy'])} | ${v['cost_per_line_usd']:.5f} | {v['avg_latency_ms']:.0f} ms |")
    L += ["", "## 3. Questions in Serbian (agent with SQL tools)", ""]
    if "skipped" in q:
        L.append(f"Skipped: {q['skipped']}.")
    else:
        L.append(f"Accuracy {_pct(q['accuracy'])} on {q['n']} questions, total cost ${q['cost_usd']:.4f}.")
        L += ["", "| Question | Expected | Correct | Tools | Cost |", "|---|---|---|---|---|"]
        for c in q["cases"]:
            L.append(f"| {c['question']} | {c['expected']} | {'yes' if c['ok'] else 'no'} | "
                     f"{', '.join(c['tools'])} | ${c['cost_usd']:.4f} |")
    L += ["", "## 4. Business case (monthly)", "", "| | Value |", "|---|---|"]
    for k, v in roi["table"]:
        L.append(f"| {k} | {v} |")
    L += ["", "Assumptions are in `sef_agent/roi.py` and can be changed per client."]
    return "\n".join(L) + "\n"
