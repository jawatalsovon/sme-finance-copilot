import datetime as dt

import pytest

from backend.engine.impl import Engine
from backend.ledger.db import connect
from backend.ledger.dev_seed import seed_dev_fixture
from tests.engine.synth import answer_key, available, build_db

AS_OF = dt.date(2026, 9, 30)


@pytest.fixture(scope="session")
def dev_engine():
    db = connect(":memory:")
    seed_dev_fixture(db)
    return Engine(db)


_ENGINES: dict = {}


def synth_engine(cid: str):
    if cid not in available():
        pytest.skip(f"synthetic data for {cid} not generated (python scripts/data/generate.py --seed 42)")
    if cid not in _ENGINES:
        _ENGINES[cid] = Engine(build_db(cid))
    return _ENGINES[cid], answer_key(cid)


SMES = ["bd_grocer", "cn_xborder", "uk_garden"]
