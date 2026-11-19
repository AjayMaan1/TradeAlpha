"""Start Historify's mock OpenAlgo as a fictitious broker with KNOWN limits (ground truth for broker_data_probe.py).
    python mock_broker.py <port> '<json config>'"""
import json, sys, time
sys.path.insert(0, "/workspace/historify-master/historify")
from tests.mock_provider.server import MockServer, configure
port, cfg = int(sys.argv[1]), json.loads(sys.argv[2])
srv = MockServer(port=port).start()
configure(**cfg)
print(f"mock broker on {srv.url} with {cfg}", flush=True)
while True:
    time.sleep(3600)
