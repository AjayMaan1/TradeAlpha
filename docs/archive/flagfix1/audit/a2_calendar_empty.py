"""A2: does a transiently EMPTY holiday response wipe a year's synced holidays?"""
import os, sys, tempfile
sys.path.insert(0, "/workspace/historify-master/historify"); os.environ.setdefault("HISTORIFY_SECRET_BACKEND", "file")
from datetime import date
from app.core.services import Services
from app.core.calendar_sync import sync_years
from app.core.calendar import MarketCalendar
class Prov:
    def __init__(self, entries): self.entries = entries
    def holidays(self, y): return self.entries
good = [{"date": "2026-01-26", "closed_exchanges": ["NSE", "BSE"], "open_exchanges": []}]
sv = Services(os.path.join(tempfile.mkdtemp(), "i"))
sync_years(sv, [2026], provider=Prov(good))
print("after good sync, 26 Jan 2026 trading?", sv.calendar().is_trading_day("NSE", date(2026, 1, 26)))
r = sync_years(sv, [2026], provider=Prov([]))
print("after EMPTY response:", r["years"], "-> 26 Jan 2026 trading?", sv.calendar().is_trading_day("NSE", date(2026, 1, 26)))
sv.close()
