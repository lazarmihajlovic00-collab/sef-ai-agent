import os
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("AS_OF_DATE", "2026-11-19")
os.environ["LLM_DISABLED"] = "1"

from sef_agent.db import Base, make_engine  # noqa: E402
from sef_agent.domain.generator import generate  # noqa: E402
from sef_agent.ingest import import_bank_statement, pull_from_sef  # noqa: E402
from sef_agent.booking.classifier import RuleClassifier  # noqa: E402
from sef_agent.pipeline import process_invoice  # noqa: E402
from sef_agent.sef.client import MockSefClient  # noqa: E402
from sef_agent import models  # noqa: E402,F401


@pytest.fixture()
def session(tmp_path: Path):
    eng = make_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    s = S()
    yield s
    s.close()


@pytest.fixture()
def seeded(session, tmp_path: Path):
    """Full demo pipeline on a temp DB: generate, pull from mock SEF, process, import payments."""
    generate(session, tmp_path, seed=7)
    from datetime import date

    new = pull_from_sef(session, MockSefClient(tmp_path / "sef_inbox"), date(2026, 1, 1), date(2026, 11, 19))
    for inv in sorted(new, key=lambda i: i.sef_id):
        process_invoice(session, inv, RuleClassifier())
    import_bank_statement(session, tmp_path / "bank_statement.json")
    session.commit()
    session.data_dir = tmp_path
    return session
