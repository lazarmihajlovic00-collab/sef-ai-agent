"""Command line: sef-agent seed | sync | eval | ask "pitanje" | serve | verify-audit"""

from __future__ import annotations

import argparse
import os
import json
from datetime import date
from pathlib import Path

from sqlalchemy import select

from .booking.classifier import RuleClassifier
from .config import settings
from .db import SessionLocal, init_db, reset_db, session_scope
from .domain.generator import TODAY, generate
from .ingest import import_bank_statement, pull_from_sef
from .models import Invoice
from .pipeline import default_classifier, process_invoice
from .sef.client import make_client

DATA = Path("data")


def cmd_seed(args) -> None:
    reset_db()
    with session_scope() as s:
        summary = generate(s, DATA, seed=args.seed)
    print("generated:", json.dumps(summary, indent=1))
    cmd_sync(args)
    with session_scope() as s:
        n = import_bank_statement(s, DATA / "bank_statement.json")
    print(f"payments imported: {n}")


def cmd_sync(args) -> None:
    init_db()
    client = make_client()
    with session_scope() as s:
        new = pull_from_sef(s, client, date(2026, 1, 1), TODAY if settings.sef_mode == "mock" else date.today())
        classifier = RuleClassifier() if getattr(args, "rules_only", False) else default_classifier(s)
        for inv in sorted(new, key=lambda i: i.sef_id):
            process_invoice(s, inv, classifier)
        statuses: dict[str, int] = {}
        for inv in s.execute(select(Invoice)).scalars():
            statuses[inv.status] = statuses.get(inv.status, 0) + 1
    print(f"pulled {len(new)} new invoices from SEF ({settings.sef_mode}); status: {statuses}")


def cmd_eval(args) -> None:
    from .eval.run import run_eval

    run_eval(DATA, classifiers=args.classifiers.split(","), with_qa=args.qa, out=Path(args.out))


def cmd_ask(args) -> None:
    from .agent.runner import ask

    with session_scope() as s:
        res = ask(s, args.question, user=args.user)
    print(res.answer)
    print(f"\n[tools: {', '.join(c['name'] for c in res.tool_calls)} | ${res.cost_usd:.4f}]")


def cmd_verify(args) -> None:
    from .audit import verify_chain

    with SessionLocal() as s:
        ok, bad = verify_chain(s)
    print("audit chain OK" if ok else f"audit chain BROKEN at event {bad}")


def cmd_serve(args) -> None:
    import uvicorn

    uvicorn.run("sef_agent.api.app:app", host=args.host, port=args.port, reload=False)


def main() -> None:
    p = argparse.ArgumentParser(prog="sef-agent")
    sub = p.add_subparsers(required=True)
    a = sub.add_parser("seed", help="reset DB, generate demo data, pull from mock SEF, process")
    a.add_argument("--seed", type=int, default=7)
    a.add_argument("--rules-only", action="store_true", help="do not call the LLM while processing")
    a.set_defaults(fn=cmd_seed)
    a = sub.add_parser("sync", help="pull new invoices from SEF and process them")
    a.add_argument("--rules-only", action="store_true")
    a.set_defaults(fn=cmd_sync)
    a = sub.add_parser("eval", help="run the evaluation set")
    a.add_argument("--classifiers", default="rules,llm")
    a.add_argument("--qa", action="store_true", help="also evaluate the Q&A agent")
    a.add_argument("--out", default="docs/eval_report.md")
    a.set_defaults(fn=cmd_eval)
    a = sub.add_parser("ask", help="ask the agent a question in Serbian")
    a.add_argument("question")
    a.add_argument("--user", default="demo.korisnik")
    a.set_defaults(fn=cmd_ask)
    a = sub.add_parser("verify-audit")
    a.set_defaults(fn=cmd_verify)
    a = sub.add_parser("serve")
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--port", type=int, default=8000)
    a.set_defaults(fn=cmd_serve)
    args = p.parse_args()
    if settings.sef_mode == "mock":
        os.environ.setdefault("AS_OF_DATE", TODAY.isoformat())  # demo data is anchored to this date
    args.fn(args)


if __name__ == "__main__":
    main()
