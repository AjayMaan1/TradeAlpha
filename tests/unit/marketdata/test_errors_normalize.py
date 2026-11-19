import pandas as pd
import pytest

from tradealpha.marketdata.providers.errors import ErrorKind, classify
from tradealpha.marketdata.providers.normalize import frame_from_payload, normalize_bars
from tradealpha.platform.time.intervals import resolve


@pytest.mark.parametrize("status,msg,kind", [
    (429, "Rate limit exceeded", ErrorKind.RATE_LIMITED),
    (403, "Access denied because of exceeding access rate", ErrorKind.RATE_LIMITED),
    (403, "Invalid Token. Please login to your broker again.", ErrorKind.AUTH),
    (403, "Invalid openalgo apikey", ErrorKind.AUTH),
    (401, "Unauthorized", ErrorKind.AUTH),
    (500, "Internal Server Error", ErrorKind.TRANSIENT),
    (502, "Bad Gateway", ErrorKind.TRANSIENT),
    (503, "Service Unavailable", ErrorKind.TRANSIENT),
    (200, "No data available for the specified period", ErrorKind.NO_DATA),
    (400, "No data found", ErrorKind.NO_DATA),
    (400, "Date range exceeds the maximum of 30 days for interval 1m", ErrorKind.PERMANENT),
    (400, "Symbol XYZ not found", ErrorKind.PERMANENT),
    (400, "Invalid interval 'W'", ErrorKind.PERMANENT),
])
def test_classify(status, msg, kind):
    assert classify(status, msg) is kind


def test_payload_accepts_seconds_millis_and_iso():
    rows = [{"timestamp": 1735669800, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
            {"timestamp": 1735756200000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
            {"timestamp": "2025-01-03T00:00:00+05:30", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]
    df = frame_from_payload(rows)
    assert df["ts"].tolist() == [1735669800, 1735756200, 1735842600]


def test_validation_rejects_bad_rows_and_dedupes():
    good = {"open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 5}
    rows = [{"timestamp": 1000 * 60, **good},
            {"timestamp": 2000 * 60, **{**good, "high": 9.5}},          # high < close -> inconsistent
            {"timestamp": 3000 * 60, **{**good, "volume": -1}},        # negative volume
            {"timestamp": 4000 * 60, **{**good, "open": None}},        # missing price
            {"timestamp": 5000 * 60, **good},
            {"timestamp": 5000 * 60, **{**good, "close": 10.8}}]       # duplicate ts -> last wins
    clean, rej = normalize_bars(frame_from_payload(rows), resolve("1m"))
    assert clean["ts"].tolist() == [60000, 300000]
    assert clean.loc[1, "close"] == 10.8
    assert sorted(rej["reason"]) == ["duplicate_timestamp", "invalid_volume", "missing_or_nan_price",
                                     "ohlc_inconsistent"]


def test_daily_bars_canonicalised():
    rows = [{"timestamp": 1735689600, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]   # 00:00 UTC
    clean, _ = normalize_bars(frame_from_payload(rows), resolve("D"))
    assert clean["ts"].tolist() == [1735669800]                                                          # 00:00 IST


def test_empty_payload():
    clean, rej = normalize_bars(frame_from_payload([]), resolve("5m"))
    assert clean.empty and rej.empty
    assert list(clean.columns) == ["ts", "open", "high", "low", "close", "volume", "oi"]
    assert isinstance(clean, pd.DataFrame)
