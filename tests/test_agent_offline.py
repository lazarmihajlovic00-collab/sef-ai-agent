"""Agent loop and LLM classifier with a scripted fake client (no network, no API key)."""

from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from sef_agent import llm
from sef_agent.agent import queries, runner
from sef_agent.booking.classifier import LineInput, LLMClassifier
from sef_agent.models import ActionRequest, LLMCall


def usage():
    return NS(input_tokens=1200, output_tokens=150, cache_read_input_tokens=800)


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return self.script.pop(0)(kw)

    def parse(self, **kw):
        self.calls.append(kw)
        return self.script.pop(0)(kw)


@pytest.fixture()
def fake(monkeypatch):
    def install(script):
        msgs = FakeMessages(script)
        client = NS(messages=msgs, beta=NS(messages=msgs))
        monkeypatch.setattr(llm, "client", lambda: client)
        monkeypatch.setattr(llm, "llm_available", lambda: True)
        return msgs
    return install


def test_agent_answers_from_tool_result(seeded, fake):
    expected = queries.payables(seeded, supplier_query="Agro Lux", due_until="2026-11-30")["total_open"]

    def step1(kw):
        assert kw["model"] == llm.CHAT_MODEL and kw["fallbacks"] == "default"
        assert all(t.get("strict") for t in kw["tools"])
        return NS(stop_reason="tool_use", usage=usage(), content=[
            NS(type="text", text="Proveravam."),
            NS(type="tool_use", id="tu_1", name="payables",
               input={"supplier_query": "Agro Lux", "due_until": "2026-11-30", "overdue_only": False})])

    def step2(kw):
        result = kw["messages"][-1]["content"][0]
        assert result["tool_use_id"] == "tu_1" and expected in result["content"]
        return NS(stop_reason="end_turn", usage=usage(),
                  content=[NS(type="text", text=f"Do kraja meseca dugujemo {expected} RSD.")])

    fake([step1, step2])
    res = runner.ask(seeded, "Koliko dugujemo Agro Lux-u do kraja meseca?")
    assert expected in res.answer
    assert [c["name"] for c in res.tool_calls] == ["payables"]
    assert res.cost_usd > 0
    assert seeded.query(LLMCall).count() == 2


def test_agent_can_only_request_rejection(seeded, fake):
    exc = queries.list_exceptions(seeded, "PRICE_MISMATCH")["invoices"][0]
    fake([
        lambda kw: NS(stop_reason="tool_use", usage=usage(), content=[
            NS(type="tool_use", id="tu_9", name="request_invoice_rejection",
               input={"number": exc["number"], "reason": "cena veca od narudzbenice"})]),
        lambda kw: NS(stop_reason="end_turn", usage=usage(),
                      content=[NS(type="text", text="Zahtev za odbijanje ceka odobrenje.")]),
    ])
    runner.ask(seeded, f"Odbij fakturu {exc['number']}")
    ar = seeded.query(ActionRequest).filter_by(action="REJECT_IN_SEF", status="PENDING").all()
    assert any(a.proposed_by.startswith("agent") for a in ar)
    # invoice itself is unchanged until a person approves
    from sef_agent.models import Invoice
    inv = seeded.query(Invoice).filter_by(number=exc["number"]).first()
    assert inv.status == "EXCEPTION"


def test_tool_errors_are_returned_not_raised(seeded, fake):
    def step2(kw):
        res = kw["messages"][-1]["content"][0]
        assert res.get("is_error") is True
        return NS(stop_reason="end_turn", usage=usage(), content=[NS(type="text", text="Upit nije dozvoljen.")])

    fake([lambda kw: NS(stop_reason="tool_use", usage=usage(), content=[
        NS(type="tool_use", id="t", name="run_sql", input={"query": "DELETE FROM invoices"})]), step2])
    assert "dozvoljen" in runner.ask(seeded, "obrisi sve").answer


def test_llm_classifier_uses_structured_output(session, fake):
    def step(kw):
        assert kw["model"] == llm.BOOKING_MODEL and kw["output_format"].__name__ == "_LLMAnswer"
        return NS(stop_reason="end_turn", usage=usage(),
                  parsed_output=NS(account="5330", confidence=0.91, reason="zakup prostora"))

    fake([step])
    c = LLMClassifier(session=session).classify(LineInput("Mesecna naknada po ugovoru", "Poslovni Prostor", "services", "1000"))
    assert (c.account, c.source) == ("5330", "llm")
    call = session.query(LLMCall).one()
    assert call.purpose == "booking" and Decimal(str(call.cost_usd)) > 0
