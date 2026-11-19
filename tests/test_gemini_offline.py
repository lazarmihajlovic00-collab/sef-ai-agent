"""Gemini provider paths with a scripted fake client (real google-genai types, no network)."""

from types import SimpleNamespace as NS

import pytest
from google.genai import types

from sef_agent import llm
from sef_agent.agent import queries, runner
from sef_agent.booking.classifier import LineInput, LLMClassifier
from sef_agent.models import LLMCall


def meta():
    return types.GenerateContentResponseUsageMetadata(prompt_token_count=900, candidates_token_count=80,
                                                     thoughts_token_count=20)


def call_resp(name, args):
    fc = types.FunctionCall(name=name, args=args)
    content = types.Content(role="model", parts=[types.Part(function_call=fc)])
    return NS(function_calls=[fc], candidates=[NS(content=content)], text=None, usage_metadata=meta())


def text_resp(text):
    return NS(function_calls=None, candidates=[], text=text, usage_metadata=meta())


class FakeModels:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def generate_content(self, **kw):
        self.calls.append(kw)
        return self.script.pop(0)(kw)


@pytest.fixture()
def gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_DISABLED", raising=False)

    def install(script):
        models = FakeModels(script)
        monkeypatch.setattr(llm, "gemini_client", lambda: NS(models=models))
        return models
    return install


def test_provider_selection(monkeypatch):
    monkeypatch.delenv("LLM_DISABLED", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert llm.provider() == "gemini" and llm.chat_model().startswith("gemini")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    assert llm.provider() == "anthropic"
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    assert llm.provider() == "gemini"


def test_gemini_agent_loop(seeded, gemini):
    expected = queries.payables(seeded, overdue_only=True)["total_open"]

    def step2(kw):
        last = kw["contents"][-1]
        fr = last.parts[0].function_response
        assert fr.name == "payables" and fr.response["result"]["total_open"] == expected
        return text_resp(f"Dospelo a neplaceno: {expected} RSD.")

    models = gemini([lambda kw: call_resp("payables", {"supplier_query": None, "due_until": None,
                                                       "overdue_only": True}), step2])
    res = runner.ask(seeded, "Koliko je dospelo a nije placeno?")
    assert expected in res.answer and [c["name"] for c in res.tool_calls] == ["payables"]
    decls = models.calls[0]["config"].tools[0].function_declarations
    assert {d.name for d in decls} >= {"payables", "run_sql", "request_invoice_rejection"}
    assert res.output_tokens == 200 and seeded.query(LLMCall).count() == 2


def test_gemini_classifier_validates_account(session, gemini):
    gemini([lambda kw: NS(parsed=NS(account="5330", confidence=0.8, reason="zakup"), usage_metadata=meta()),
            lambda kw: NS(parsed=NS(account="9999", confidence=0.9, reason="?"), usage_metadata=meta())])
    clf = LLMClassifier(session=session)
    li = LineInput("Najam parking mesta", "Poslovni Prostor", "services", "100")
    assert clf.classify(li).account == "5330"
    bad = clf.classify(li)
    assert bad.confidence == 0.0  # an account outside the chart is never accepted
