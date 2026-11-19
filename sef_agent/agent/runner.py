"""Conversational agent: answers accounts-payable questions in Serbian.

Manual tool-use loop on the Messages API. The agent has read-only tools plus one
tool that *requests* an action (rejecting an invoice in SEF); that request waits
for a person's approval in the dashboard, the agent cannot execute it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .. import llm
from ..models import Invoice
from ..pipeline import request_rejection
from . import queries

MAX_TURNS = 8

SYSTEM_PROMPT = f"""Ti si AI asistent za ulazne e-fakture (SEF) u preduzecu Demo Trgovina d.o.o.
Odgovaras zaposlenima u racunovodstvu i nabavci, na srpskom jeziku (latinica), kratko i precizno.

Pravila:
- Svaki iznos, broj i datum u odgovoru mora poticati iz rezultata alata. Ne racunaj zbirove napamet
  i ne izmisljaj podatke. Ako alat ne vrati podatak, reci da ga nema.
- Za dugovanja koristi alat payables; za trosak po dobavljacu spend_by_supplier; za fakture sa
  odstupanjima list_exceptions; za detalje jedne fakture get_invoice. run_sql koristi samo kada
  nijedan drugi alat ne odgovara na pitanje.
- "Do kraja meseca" znaci do poslednjeg dana tekuceg meseca u odnosu na datum as_of.
- Iznose pisi u formatu 1.234.567,89 RSD. Kada je deo iznosa u fakturama sa odstupanjima, to napomeni.
- Ne mozes sam da proknjizis, platis, prihvatis ili odbijes fakturu. Mozes samo da zatrazis odbijanje
  alatom request_invoice_rejection, i onda jasno reci da zahtev ceka odobrenje odgovorne osobe.

Sema baze (za run_sql, SQLite/PostgreSQL):
{queries.SCHEMA_DOC}
"""


def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": props, "required": required,
                             "additionalProperties": False}}


_NULLABLE_STR = {"type": ["string", "null"]}

TOOLS = [
    _tool("payables", "Otvorena (neplacena) dugovanja prema dobavljacima. Vraca ukupan iznos, deo u spornim "
          "fakturama i listu faktura. due_until je ISO datum (dospece do ukljucivo).",
          {"supplier_query": {**_NULLABLE_STR, "description": "deo naziva ili PIB dobavljaca, null za sve"},
           "due_until": {**_NULLABLE_STR, "description": "YYYY-MM-DD ili null"},
           "overdue_only": {"type": "boolean", "description": "true = samo vec dospele na danasnji dan"}},
          ["supplier_query", "due_until", "overdue_only"]),
    _tool("spend_by_supplier", "Trosak (osnovica bez PDV-a) po dobavljacu u periodu, sortirano opadajuce.",
          {"date_from": {"type": "string"}, "date_to": {"type": "string"},
           "limit": {"type": "integer"}}, ["date_from", "date_to", "limit"]),
    _tool("list_exceptions", "Fakture koje cekaju proveru zbog odstupanja, sa razlozima. finding_type npr. "
          "PRICE_MISMATCH, QTY_NOT_RECEIVED, DUPLICATE, POSSIBLE_DUPLICATE, BANK_ACCOUNT_CHANGED, NO_PO.",
          {"finding_type": _NULLABLE_STR, "supplier_query": _NULLABLE_STR}, ["finding_type", "supplier_query"]),
    _tool("get_invoice", "Detalji jedne fakture po broju fakture ili SEF ID-u: stavke, nalazi, predlog knjizenja.",
          {"number": {"type": "string"}}, ["number"]),
    _tool("run_sql", "Jedan SELECT upit nad bazom (samo citanje, najvise 50 redova).",
          {"query": {"type": "string"}}, ["query"]),
    _tool("request_invoice_rejection", "Kreira ZAHTEV za odbijanje fakture u SEF-u. Zahtev ne izvrsava nista "
          "dok ga odgovorna osoba ne odobri.",
          {"number": {"type": "string"}, "reason": {"type": "string"}}, ["number", "reason"]),
]
TOOLS[-1]["cache_control"] = {"type": "ephemeral"}  # tools + system are a stable, cacheable prefix


@dataclass
class AgentResult:
    answer: str
    tool_calls: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


def _execute(session: Session, name: str, args: dict, user: str) -> dict:
    if name == "payables":
        return queries.payables(session, args.get("supplier_query"), args.get("due_until"),
                                bool(args.get("overdue_only")))
    if name == "spend_by_supplier":
        return queries.spend_by_supplier(session, args["date_from"], args["date_to"], int(args.get("limit") or 10))
    if name == "list_exceptions":
        return queries.list_exceptions(session, args.get("finding_type"), args.get("supplier_query"))
    if name == "get_invoice":
        return queries.get_invoice(session, args["number"])
    if name == "run_sql":
        return queries.run_sql(session, args["query"])
    if name == "request_invoice_rejection":
        from sqlalchemy import select

        inv = session.execute(select(Invoice).where(Invoice.number == args["number"])).scalars().first()
        if inv is None:
            return {"error": "faktura nije pronadjena"}
        ar = request_rejection(session, inv, f"agent (za {user})", args["reason"])
        return {"action_id": ar.id, "status": "PENDING", "note": "ceka odobrenje odgovorne osobe"}
    return {"error": f"nepoznat alat {name}"}


def ask(session: Session, question: str, user: str = "korisnik", history: list | None = None) -> AgentResult:
    if not llm.llm_available():
        return AgentResult("LLM nije ukljucen (nema API kljuca). Ostatak sistema radi bez njega.")
    prompt = f"(as_of: {queries.as_of().isoformat()})\n{question}"
    t0 = time.perf_counter()
    if llm.provider() == "gemini":
        result = _ask_gemini(session, prompt, user)
    else:
        result = _ask_anthropic(session, prompt, user, history)
    result.latency_ms = int((time.perf_counter() - t0) * 1000)
    return result


def _add_usage(result: AgentResult, model: str, resp) -> None:
    inp, out, cache = llm.usage_of(resp)
    result.input_tokens += inp
    result.output_tokens += out
    result.cost_usd += llm.cost_usd(model, inp, out, cache)


def _ask_anthropic(session: Session, prompt: str, user: str, history: list | None) -> AgentResult:
    model = llm.chat_model()
    messages = list(history or []) + [{"role": "user", "content": prompt}]
    result = AgentResult(answer="")
    for _ in range(MAX_TURNS):
        with llm.track(session, "chat", model) as rec:
            resp = llm.client().beta.messages.create(
                model=model,
                max_tokens=16000,
                system=[{"type": "text", "text": SYSTEM_PROMPT}],
                tools=TOOLS,
                messages=messages,
                output_config={"effort": llm_effort()},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            rec(resp)
        _add_usage(result, model, resp)
        if resp.stop_reason == "refusal":
            result.answer = "Model je odbio ovaj zahtev."
            break
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "pause_turn":
            continue
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not tool_uses:
            result.answer = "\n".join(b.text for b in resp.content if b.type == "text").strip()
            break
        results = []
        for tu in tool_uses:
            out = _execute(session, tu.name, tu.input, user)
            result.tool_calls.append({"name": tu.name, "input": tu.input})
            results.append({"type": "tool_result", "tool_use_id": tu.id,
                            "content": json.dumps(out, ensure_ascii=False, default=str),
                            **({"is_error": True} if "error" in out else {})})
        messages.append({"role": "user", "content": results})
    else:
        result.answer = result.answer or "Nisam uspeo da zavrsim odgovor u dozvoljenom broju koraka."
    return result


def _gemini_tools():
    from google.genai import types

    decls = [types.FunctionDeclaration(name=t["name"], description=t["description"],
                                       parameters_json_schema=t["input_schema"]) for t in TOOLS]
    return [types.Tool(function_declarations=decls)]


def _ask_gemini(session: Session, prompt: str, user: str) -> AgentResult:
    from google.genai import types

    model = llm.chat_model()
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=_gemini_tools(),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    contents: list = [types.Content(role="user", parts=[types.Part(text=prompt)])]
    result = AgentResult(answer="")
    for _ in range(MAX_TURNS):
        with llm.track(session, "chat", model) as rec:
            resp = llm.gemini_client().models.generate_content(model=model, contents=contents, config=config)
            rec(resp)
        _add_usage(result, model, resp)
        calls = resp.function_calls or []
        if not calls:
            result.answer = (resp.text or "").strip() or "Model nije vratio odgovor."
            break
        contents.append(resp.candidates[0].content)
        parts = []
        for fc in calls:
            args = dict(fc.args or {})
            out = _execute(session, fc.name, args, user)
            result.tool_calls.append({"name": fc.name, "input": args})
            parts.append(types.Part.from_function_response(
                name=fc.name, response={"result": json.loads(json.dumps(out, ensure_ascii=False, default=str))}))
        contents.append(types.Content(role="user", parts=parts))
    else:
        result.answer = result.answer or "Nisam uspeo da zavrsim odgovor u dozvoljenom broju koraka."
    return result


def llm_effort() -> str:
    import os

    return os.environ.get("CHAT_EFFORT", "medium")
