"""Metric: per-request cost of the request guard (WSGI layer) - call the guard vs the bare app directly."""
import os, sys, time, statistics, tempfile
sys.path.insert(0, "/workspace/historify-master/historify"); os.environ.setdefault("HISTORIFY_SECRET_BACKEND", "file")
from app.core.services import Services
from app import create_app
from app.core.web_guard import RequestGuard
sv = Services(os.path.join(tempfile.mkdtemp(), "i"))
app = create_app({"HISTORIFY_SERVICES": sv, "TESTING": True}, instance_path=sv.instance_path)
guarded = app.wsgi_app; bare = guarded.app
env = {"REQUEST_METHOD": "POST", "PATH_INFO": "/healthz-not-found", "HTTP_HOST": "127.0.0.1:5001",
       "HTTP_ORIGIN": "http://127.0.0.1:5001", "SERVER_NAME": "127.0.0.1", "SERVER_PORT": "5001", "wsgi.url_scheme": "http",
       "wsgi.input": None, "SERVER_PROTOCOL": "HTTP/1.1"}
import io
def call(w, n=3000):
    t0 = time.perf_counter()
    for _ in range(n):
        e = dict(env); e["wsgi.input"] = io.BytesIO(b"")
        list(w(e, lambda *a, **k: None))
    return (time.perf_counter() - t0) / n * 1e6
for w in (bare, guarded): call(w, 300)
b = statistics.median(call(bare) for _ in range(5)); g = statistics.median(call(guarded) for _ in range(5))
print(f"bare app: {b:.1f} us/request  guarded: {g:.1f} us/request  overhead: {g-b:.1f} us ({(g-b)/b:.1%})")
sv.close()
