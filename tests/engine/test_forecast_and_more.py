import datetime as dt
import time
from decimal import Decimal

import pytest

from backend.engine.interfaces import EngineError
from backend.engine.types import ScenarioChange
from backend.testing import make_client, make_container
from backend.engine.impl import Engine
from tests.engine.conftest import AS_OF, SMES, synth_engine


# ------------------------------------------------------------------ forecast
@pytest.mark.parametrize("cid", SMES)
def test_forecast_13_weeks_and_12_months_with_backtest(cid):
    eng, _ = synth_engine(cid)
    eng.cash_truth(cid, AS_OF)          # the forecast starts from verified cash; time only the forecast itself
    eng._corrections(cid)
    t = time.time()
    w = eng.cashflow_forecast(cid, 13, "week", AS_OF)
    m = eng.cashflow_forecast(cid, 12, "month", AS_OF)
    assert time.time() - t < 10        # "a few seconds per SME", both horizons with backtests
    for fc, n in ((w, 13), (m, 12)):
        assert len(fc.points) == n and fc.basis == "corrected"
        assert fc.opening_cash.value == eng.cash_truth(cid, AS_OF).verified_cash.value
        for p in fc.points:
            assert p.closing_cash_lower <= p.closing_cash <= p.closing_cash_upper
            assert p.closing_cash_lower_95 <= p.closing_cash_lower and p.closing_cash_upper <= p.closing_cash_upper_95
            assert p.net == p.inflow - p.outflow
        assert fc.backtest and fc.backtest.folds >= 10 and fc.backtest.metric == "sMAPE"
        assert 0 < fc.backtest.value < 40, fc.backtest
        # bands widen with the horizon
        assert (fc.points[-1].closing_cash_upper - fc.points[-1].closing_cash_lower) >= \
            (fc.points[0].closing_cash_upper - fc.points[0].closing_cash_lower)
    assert w.points[0].period_start > AS_OF - dt.timedelta(days=7)


def test_forecast_rejects_bad_arguments(dev_engine):
    with pytest.raises(EngineError):
        dev_engine.cashflow_forecast("devco", 13, "day")


def test_holiday_factor_found_for_bangladesh():
    eng, _ = synth_engine("bd_grocer")
    fc = eng.cashflow_forecast("bd_grocer", 12, "month", AS_OF)
    assert any("Eid" in a or "Ramadan" in a for a in fc.assumptions), fc.assumptions


# ------------------------------------------------------------------ alerts, financing, scenarios, brief
@pytest.mark.parametrize("cid", SMES)
def test_alerts_have_evidence_and_gap_alert(cid):
    eng, _ = synth_engine(cid)
    al = eng.alerts(cid, AS_OF)
    kinds = {a.kind for a in al.alerts}
    assert "cash_truth_gap" in kinds        # all three demo SMEs have a material gap (> 0.5% of revenue)
    for a in al.alerts:
        assert a.id and a.message and a.severity in ("info", "warning", "critical")
        if a.kind in ("late_customer", "bill_due", "cash_truth_gap", "unusual_expense"):
            assert a.evidence, a.title


@pytest.mark.parametrize("cid", SMES)
def test_financing_matches_country_rules_and_affordability(cid):
    eng, _ = synth_engine(cid)
    f = eng.financing_matches(cid, AS_OF)
    L = eng._ledger(cid)
    assert f.matches and all(m.product.country == L.country for m in f.matches)
    assert all(m.product.illustrative for m in f.matches) and "never applies" in f.disclaimer
    scores = [m.fit_score for m in f.matches]
    assert scores == sorted(scores, reverse=True)
    loans = [m for m in f.matches if m.affordability]
    assert loans
    for m in loans:
        a = m.affordability
        assert a.monthly_repayment > 0 and a.explanation
        if a.affordable:
            assert a.dscr >= 1.25
    assert any(m.eligible for m in f.matches)


def test_unknown_facts_are_never_passed():
    eng, _ = synth_engine("cn_xborder")
    f = eng.financing_matches("cn_xborder", AS_OF)
    webank = next(m for m in f.matches if "webank" in m.product.id)
    owner_age = [c for c in webank.checks if c.rule.startswith("owner_age")]
    assert owner_age and not any(c.passed for c in owner_age)
    assert "please confirm" in owner_age[0].reason


@pytest.mark.parametrize("cid", SMES)
def test_three_scenario_presets(cid):
    eng, _ = synth_engine(cid)
    base_end = eng.cashflow_forecast(cid, 13, "week", AS_OF).points[-1].closing_cash
    up = eng.scenario(cid, [ScenarioChange(kind="cogs_pct", value=15)], AS_OF)
    assert up.scenario.points[-1].closing_cash < base_end
    assert up.baseline.points[-1].closing_cash == base_end and up.deltas[0].value < 0
    fx = eng.scenario(cid, [ScenarioChange(kind="fx_pct", value=-10)], AS_OF)
    assert fx.scenario.points[-1].closing_cash <= base_end
    if cid == "cn_xborder":                       # sells in USD/EUR/GBP: an FX move must matter
        assert fx.deltas[0].value < 0
    lose = eng.scenario(cid, [ScenarioChange(kind="lose_customer", value=0)], AS_OF)
    assert lose.scenario.points[-1].closing_cash <= base_end and lose.summary
    with pytest.raises(EngineError):
        eng.scenario(cid, [ScenarioChange(kind="new_loan", value=1000)], AS_OF)
    assert set(Engine.scenario_presets()) == {"input_cost_up", "lose_biggest_customer", "fx_move"}


def test_owner_brief_links_actions_to_drafts():
    eng, _ = synth_engine("cn_xborder")
    b = eng.owner_brief("cn_xborder", AS_OF)
    ct = eng.cash_truth("cn_xborder", AS_OF)
    assert b.cash_truth_gap.value == ct.gap.value and len(b.top_alerts) <= 3
    drafts = [a for a in b.actions if a.kind == "approve_draft"]
    assert drafts and all(a.draft and a.finding_id for a in drafts)
    ids = {f.id for f in ct.findings}
    assert all(a.finding_id in ids for a in drafts)
    assert b.summary.startswith("Owner Brief for the week") and str(AS_OF) in b.summary


# ------------------------------------------------------------------ API (uvicorn app, mock LLM)
def test_api_reports_return_200_with_evidence():
    with make_client(make_container()) as client:
        r = client.get("/reports/devco/cash-truth?as_of=2026-09-30")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["findings"] and all(f["evidence"] for f in body["findings"])
        for path in ["/reports/devco/pnl?month=2026-09&basis=corrected", "/reports/devco/kpis?as_of=2026-09-30",
                     "/reports/devco/alerts?as_of=2026-09-30", "/reports/devco/financing"]:
            assert client.get(path).status_code == 200, path


def test_owner_brief_summaries_in_three_languages_carry_the_same_numbers():
    from backend.core.util import format_money
    eng, _ = synth_engine("cn_xborder")
    b = eng.owner_brief("cn_xborder", AS_OF)
    assert set(b.summaries) == {"en", "zh", "bn"} and b.summaries["en"] == b.summary
    gap = format_money(abs(b.cash_truth_gap.value), b.currency)
    for lang in ("en", "zh", "bn"):
        assert gap in b.summaries[lang], lang          # numbers come from the engine, not translated
        assert str(b.week_start) in b.summaries[lang]
    assert "老板简报" in b.summaries["zh"] and "সারসংক্ষেপ" in b.summaries["bn"]
