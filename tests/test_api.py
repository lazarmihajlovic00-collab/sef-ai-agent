from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from sef_agent.api import app as app_module


def test_api_flow(seeded):
    factory = sessionmaker(bind=seeded.get_bind(), expire_on_commit=False)

    def db():
        s = factory()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    app_module.app.dependency_overrides[app_module.db] = db
    c = TestClient(app_module.app)
    summ = c.get("/api/summary").json()
    assert summ["by_status"]["EXCEPTION"] > 0 and not summ["llm_enabled"]
    acts = c.get("/api/actions").json()
    assert acts[0]["action"] == "REJECT_IN_SEF"  # risky items first
    r = c.post(f"/api/actions/{acts[0]['id']}/decide", json={"approve": False}, headers={"X-User": "agent"})
    assert r.status_code == 400
    r = c.post(f"/api/actions/{acts[0]['id']}/decide", json={"approve": False, "comment": "proveriti"},
               headers={"X-User": "ana.petrovic"})
    assert r.json()["status"] == "DENIED"
    assert c.get("/api/audit/verify").json()["ok"]
    inv = c.get(f"/api/invoices/{acts[0]['invoice']['id']}").json()
    assert any(h["actor"] == "ana.petrovic" for h in inv["history"])
    assert c.get("/api/roi?invoices_per_month=1000").json()["hours_saved"] > 0
    app_module.app.dependency_overrides.clear()
