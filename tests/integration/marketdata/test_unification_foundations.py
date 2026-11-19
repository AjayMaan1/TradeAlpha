"""Unification U1 - shared foundations: one corporate-action view, one cost source, one point-in-time rule."""
from datetime import date

import pandas as pd

from tests.conftest import add_instrument


def _ref(s, symbol, ex, kind, factor=None, cash=None, status="verified", key="k"):
    from tradealpha.platform.db.models import CorporateAction
    s.add(CorporateAction(symbol=symbol, ex_date=ex, kind=kind, price_factor=factor, cash=cash, status=status,
                          source="nse", source_key=f"{key}-{ex}-{kind}"))


def test_one_corporate_action_view_for_both_consumers(make_services):
    from tradealpha.marketdata.refdata import corporate
    from tradealpha.marketdata.refdata import instrument_ca as ca
    from tradealpha.platform.db.models import InstrumentCorporateAction
    sv = make_services(start=False)
    iid = add_instrument(sv, "ACME")
    with sv.meta.session() as s:
        _ref(s, "ACME", date(2024, 3, 1), "split", factor=0.5)                   # exchange master (flagfix2)
        _ref(s, "ACME", date(2024, 6, 3), "bonus", factor=0.8, key="a")
        _ref(s, "ACME", date(2024, 9, 2), "split", factor=0.1, status="unparsed")  # never used
        s.add(InstrumentCorporateAction(instrument_id=iid, ex_date=date(2024, 6, 3), kind="bonus", ratio_num=1,
                                        ratio_den=1, price_factor=0.5, source="manual"))   # the user's correction
        s.add(InstrumentCorporateAction(instrument_id=iid, ex_date=date(2024, 8, 1), kind="dividend",
                                        cash_amount=5.0, source="nse_csv"))
        s.commit()
        ev = ca.events_for(s, iid)
        own_only = ca.events_for(s, iid, include_reference=False)
    assert [(e.ex_date.isoformat(), e.kind, e.price_factor, e.source) for e in ev] == [
        ("2024-03-01", "split", 0.5, "refdata:nse"),          # from the master
        ("2024-06-03", "bonus", 0.5, "manual"),               # the manual event wins on the same date
        ("2024-08-01", "dividend", None, "nse_csv")]
    assert len(own_only) == 2
    f = corporate.factors(sv, "ACME")                         # the P3 / P4 path sees the same set
    assert [(d.isoformat(), round(x, 6)) for d, x in zip(f["ex_date"], f["factor"], strict=True)] == [
        ("2024-03-01", 0.5), ("2024-06-03", 0.5)]
    bars = pd.DataFrame({"ts": [1706745600, 1717200000, 1725148800], "open": [100.0] * 3, "high": [100.0] * 3,
                         "low": [100.0] * 3, "close": [100.0] * 3, "volume": [1000] * 3})
    adj = ca.adjust_frame(bars, ev, "adjusted")               # Feb 2024, Jun 1 2024, Sep 2024
    assert adj["close"].tolist() == [25.0, 50.0, 100.0] and adj["volume"].tolist() == [4000, 2000, 1000]
    tot = ca.adjust_frame(bars, ev, "total_return")
    assert tot["volume"].tolist() == [4000, 2000, 1000]       # dividends change prices, never volumes


def test_the_bars_api_uses_the_merged_view(make_v2):
    c, sv, _ = make_v2()
    iid = add_instrument(sv, "ACME")
    with sv.meta.session() as s:
        _ref(s, "ACME", date(2025, 3, 12), "split", factor=0.5)
        s.commit()
    from datetime import datetime

    from tradealpha.platform.time.intervals import INTERVALS
    from tradealpha.platform.time.timeutil import IST
    rows = []
    for k, d in enumerate([date(2025, 3, 10), date(2025, 3, 11), date(2025, 3, 12), date(2025, 3, 13)]):
        ts = int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp())
        px = 200.0 if k < 2 else 100.0
        rows.append({"ts": ts, "open": px, "high": px, "low": px, "close": px, "volume": 100, "oi": 0})
    sv.store.upsert(iid, INTERVALS["D"].id, pd.DataFrame(rows))
    raw = c.get(f"/api/v2/bars?instrument_id={iid}&interval=D&adjust=raw").get_json()
    for view in ("split", "adjusted"):                        # both vocabularies
        adj = c.get(f"/api/v2/bars?instrument_id={iid}&interval=D&adjust={view}").get_json()
        assert adj["bars"]["c"] == [100.0, 100.0, 100.0, 100.0] and adj["adjust_source"] == "instrument_events"
    assert raw["bars"]["c"] == [200.0, 200.0, 100.0, 100.0]
