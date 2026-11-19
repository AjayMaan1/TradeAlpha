"""OpenAlgoProvider against the Angel One mock (DL-02, DL-03, DL-07, DL-09, auth breaker)."""
import time
from datetime import date

import pytest

from tests.support.mock_provider.server import expected_bars
from tradealpha.marketdata.bars.base import checksum_frame
from tradealpha.marketdata.providers.errors import ErrorKind, ProviderError
from tradealpha.marketdata.providers.normalize import frame_from_payload, normalize_bars
from tradealpha.marketdata.providers.openalgo.client import OpenAlgoProvider
from tradealpha.platform.ratelimit.profiles import ANGELONE
from tradealpha.platform.time.intervals import resolve


@pytest.fixture
def provider(mock_server):
    p = OpenAlgoProvider(mock_server.url, "k", ANGELONE, backoff_base_s=0.05, timeout_s=10)
    yield p
    p.close()


def _oracle(sym, ex, iv, s, e):
    clean, _ = normalize_bars(frame_from_payload(expected_bars(sym, ex, iv, s, e)), resolve(iv))
    return clean


@pytest.mark.parametrize("iv,start,end,chunks", [
    ("1m", "2025-01-01", "2025-03-31", 3),       # > 30-day cap
    ("5m", "2024-10-01", "2025-03-31", 2),       # > 100-day cap
    ("D", "2015-01-01", "2025-03-31", 2),        # > 2000-day cap
])
def test_chunking_respects_angel_caps_and_matches_oracle(mock_server, provider, iv, start, end, chunks):
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    res = provider.history("SBIN", "NSE", iv, s, e)
    assert res.chunks == chunks and res.requests == chunks
    assert mock_server.stats["history_range_err"] == 0 and mock_server.stats["history_429"] == 0
    assert checksum_frame(res.bars) == checksum_frame(_oracle("SBIN", "NSE", iv, s, e))


def test_no_data_is_empty_not_error(mock_server, provider):
    res = provider.history("SBIN", "NSE", "D", date(2025, 3, 15), date(2025, 3, 16))   # weekend
    assert res.empty and res.empty_chunks == 1


def test_timeout_longer_than_sdk_default_succeeds(mock_server, provider):
    mock_server.configure(latency_by_symbol={"SLOWCO": 6.5})
    t = time.perf_counter()
    res = provider.history("SLOWCO", "NSE", "D", date(2025, 3, 3), date(2025, 3, 7))
    assert len(res.bars) == 5 and time.perf_counter() - t >= 6


def test_transient_errors_are_retried(mock_server, provider):
    mock_server.configure(error_rate=0.3, seed=7)
    for i in range(10):
        res = provider.history(f"SYM{i}", "NSE", "D", date(2025, 3, 3), date(2025, 3, 7))
        assert len(res.bars) == 5
    assert mock_server.stats["5xx"] > 0
    assert provider.stats["retries"] == mock_server.stats["5xx"]


def test_rate_limit_compliance_no_429(mock_server, provider):
    t = time.perf_counter()
    for i in range(12):
        provider.history(f"R{i}", "NSE", "D", date(2025, 3, 3), date(2025, 3, 7))
    elapsed = time.perf_counter() - t
    assert mock_server.stats["history_429"] == 0
    assert elapsed >= 11 / 2.4 - 0.1                          # 80% of 3 req/s


def test_429_is_retried_after_block(mock_server):
    # A provider with a permissive limiter hits the broker limit, then recovers via RATE_LIMITED retries.
    from tradealpha.platform.ratelimit.limiter import MultiWindowRateLimiter
    p = OpenAlgoProvider(mock_server.url, "k", ANGELONE, backoff_base_s=0.05,
                         history_limiter=MultiWindowRateLimiter([(100, 1.0)]))
    try:
        for i in range(8):
            assert len(p.history(f"B{i}", "NSE", "D", date(2025, 3, 3), date(2025, 3, 7)).bars) == 5
        assert mock_server.stats["history_429"] > 0
        assert p.stats["errors"]["rate_limited"] == mock_server.stats["history_429"]
    finally:
        p.close()


def test_auth_failure_opens_circuit(mock_server, provider):
    mock_server.configure(session_valid=False)
    for i in range(5):
        with pytest.raises(ProviderError) as ei:
            provider.history(f"A{i}", "NSE", "D", date(2025, 3, 3), date(2025, 3, 7))
        assert ei.value.kind is ErrorKind.AUTH
    assert mock_server.stats["auth_err"] == 1                 # only the first call reached the broker
    mock_server.configure(session_valid=True)
    provider.reset_auth_circuit()                             # user re-logged in + "test connection"
    assert len(provider.history("A9", "NSE", "D", date(2025, 3, 3), date(2025, 3, 7)).bars) == 5


def test_permanent_errors_not_retried(mock_server, provider):
    with pytest.raises(ProviderError) as ei:
        provider.history("INVALIDSYM", "NSE", "D", date(2025, 3, 3), date(2025, 3, 7))
    assert ei.value.kind is ErrorKind.PERMANENT and mock_server.stats["history_requests"] == 1
    with pytest.raises(ProviderError) as ei:
        provider.history("SBIN", "NSE", "W", date(2025, 3, 3), date(2025, 3, 7))
    assert ei.value.kind is ErrorKind.PERMANENT and mock_server.stats["history_requests"] == 1   # never sent


@pytest.mark.parametrize("conv", ["ist_midnight", "utc_midnight", "ist_open"])
def test_daily_conventions_normalise_identically(mock_server, provider, conv):
    mock_server.configure(daily_ts=conv)
    res = provider.history("RELIANCE", "NSE", "D", date(2025, 1, 1), date(2025, 1, 31))
    mock_server.configure(daily_ts="ist_midnight")
    ref = _oracle("RELIANCE", "NSE", "D", date(2025, 1, 1), date(2025, 1, 31))
    assert checksum_frame(res.bars) == checksum_frame(ref)
    assert provider.detect_daily_convention("RELIANCE", "NSE")["convention"] in ("ist_midnight", "unknown")


def test_connection_refused_is_transient(provider):
    p = OpenAlgoProvider("http://127.0.0.1:9", "k", ANGELONE, max_retries=1, backoff_base_s=0.01, timeout_s=2)
    with pytest.raises(ProviderError) as ei:
        p.history("SBIN", "NSE", "D", date(2025, 3, 3), date(2025, 3, 7))
    assert ei.value.kind is ErrorKind.TRANSIENT and p.stats["requests"] == 2
    p.close()
