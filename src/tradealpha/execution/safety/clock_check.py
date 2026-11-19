"""
Clock discipline (trading-engine Phase 4, L4): an SNTP (RFC 4330) client that measures this host's clock offset and
feeds guardrail G-SYS-3 (``max_clock_offset_s``, default 1 s: entries are blocked while the clock is off by more).

* ``ntp_server`` setting: "host" or "host:port" (default port 123); unset = not measured (G-SYS-3 passes, and the
  live gate's clock criterion fails - a live deployment needs a measured clock);
* the offset is re-measured every ``REFRESH_S`` seconds, lazily (on read, never on the order path's critical section:
  a stale value is returned while the refresh runs in a background thread);
* an unreachable server keeps the last offset for ``STALE_S`` seconds, then reports None with the error.

The operating system should still run chrony / systemd-timesyncd (deploy/ docs); this check is the engine's own
verification that it did.
"""
from __future__ import annotations

import logging
import socket
import struct
import threading
import time

log = logging.getLogger(__name__)
NTP_DELTA = 2_208_988_800            # 1900-01-01 -> 1970-01-01
REFRESH_S = 300.0
STALE_S = 1800.0


def _to_ntp(t: float) -> tuple[int, int]:
    sec = int(t) + NTP_DELTA
    return sec, int((t - int(t)) * 2**32)


def _from_ntp(sec: int, frac: int) -> float:
    return sec - NTP_DELTA + frac / 2**32


def sntp_offset(host: str, port: int = 123, timeout: float = 2.0, now=time.time) -> tuple[float, float]:
    """One SNTP exchange -> (offset_s, round_trip_s); offset = server time - local time (RFC 4330 formula)."""
    pkt = bytearray(48)
    pkt[0] = (0 << 6) | (4 << 3) | 3                  # LI 0, version 4, mode 3 (client)
    t1 = now()
    s_, f_ = _to_ntp(t1)
    struct.pack_into("!II", pkt, 40, s_, f_)          # transmit timestamp (echoed as originate)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(bytes(pkt), (host, int(port)))
        data, _ = sock.recvfrom(512)
    t4 = now()
    if len(data) < 48:
        raise OSError("short NTP reply")
    mode, stratum = data[0] & 7, data[1]
    if mode not in (4, 5) or stratum == 0:
        raise OSError(f"NTP server refused (mode {mode}, stratum {stratum} - kiss-of-death?)")
    t2 = _from_ntp(*struct.unpack_from("!II", data, 32))
    t3 = _from_ntp(*struct.unpack_from("!II", data, 40))
    return ((t2 - t1) + (t3 - t4)) / 2.0, (t4 - t1) - (t3 - t2)


class ClockCheck:
    def __init__(self, services, measure=sntp_offset):
        self.sv = services
        self.measure = measure
        self._lock = threading.Lock()
        self._offset: float | None = None
        self._rtt: float | None = None
        self._at = 0.0
        self._ok_at = 0.0
        self._error: str | None = None
        self._busy = False

    def server(self) -> tuple[str, int] | None:
        v = str(self.sv.settings.get("ntp_server", None) or "").strip()
        if not v:
            return None
        host, _, port = v.partition(":")
        return host, int(port or 123)

    def refresh(self) -> dict:
        srv = self.server()
        if srv is None:
            with self._lock:
                self._offset, self._error = None, "ntp_server not set"
            return self.status()
        try:
            off, rtt = self.measure(*srv)
            with self._lock:
                self._offset, self._rtt, self._ok_at, self._error = off, rtt, time.monotonic(), None
        except (OSError, ValueError) as e:
            with self._lock:
                self._error = f"{type(e).__name__}: {e}"
            log.warning("clock check against %s failed: %s", srv, e)
        finally:
            with self._lock:
                self._at = time.monotonic()
        return self.status()

    def _maybe_refresh(self) -> None:
        with self._lock:
            due = time.monotonic() - self._at >= REFRESH_S and not self._busy and self.server() is not None
            if due:
                self._busy = True
        if due:
            def run():
                try:
                    self.refresh()
                finally:
                    self._busy = False
            threading.Thread(target=run, name="clock-check", daemon=True).start()

    def offset(self) -> float | None:
        """The last measured offset (seconds; None = unknown / stale). Used as ``PaperService.clock_offset_fn``."""
        self._maybe_refresh()
        with self._lock:
            if self._offset is None or time.monotonic() - self._ok_at > STALE_S:
                return None
            return float(self._offset)

    def last_offset(self) -> float | None:
        with self._lock:
            return self._offset if self._offset is not None and time.monotonic() - self._ok_at <= STALE_S else None

    def status(self) -> dict:
        srv = self.server()
        with self._lock:
            return {"server": f"{srv[0]}:{srv[1]}" if srv else None, "offset_s": self._offset,
                    "round_trip_s": self._rtt, "error": self._error,
                    "age_s": round(time.monotonic() - self._ok_at, 1) if self._ok_at else None}
