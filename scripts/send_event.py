"""Sign and send one fictional webhook using only the Python standard library."""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hookwatch.config import settings
from hookwatch.security import sign

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:5000/api/events")
parser.add_argument(
    "--id", default=None, help="Reuse this ID to demonstrate deduplication"
)
parser.add_argument("--failures", type=int, default=0, choices=range(4))
args = parser.parse_args()
config = settings()
if not config["WEBHOOK_SECRET"]:
    parser.error("Set WEBHOOK_SECRET or copy .env.example to .env")
payload = {
    "id": args.id or f"evt_{uuid.uuid4().hex}",
    "source": "demo-cli",
    "type": "release.created",
    "data": {"release_id": "rel_sample", "title": "Night Transit"},
    "simulate_failures": args.failures,
}
raw = json.dumps(payload).encode()
timestamp = str(int(time.time()))
request = urllib.request.Request(
    args.url,
    data=raw,
    headers={
        "Content-Type": "application/json",
        "X-Webhook-Timestamp": timestamp,
        "X-Webhook-Signature": sign(config["WEBHOOK_SECRET"], timestamp, raw),
    },
)
try:
    with urllib.request.urlopen(request, timeout=10) as response:
        print(response.status, response.read().decode())
except urllib.error.HTTPError as error:
    print(error.code, error.read().decode(), file=sys.stderr)
    raise SystemExit(1)
