"""Point Photon at the deploy: one webhook destination for message.received -> <site>/photon.

  python deploy/photon_webhook.py https://<your-site>.vercel.app

Photon shows the signing secret only once, in the create response: it goes straight into .env as PHOTON_WEBHOOK_SECRET
(never printed). Then run deploy/push_env.sh and redeploy so the site can check signatures.
Running it again for the same URL replays the same create (same idempotency key) instead of making a second destination.
"""
import hashlib, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))
from primesight.photon_bridge import Photon  # noqa: E402

site = (sys.argv[1] if len(sys.argv) > 1 else "").rstrip("/")
assert re.fullmatch(r"https://[\w.-]+", site), "usage: python deploy/photon_webhook.py https://<your-site>.vercel.app"
p = Photon(os.environ["PHOTON_API_KEY"], os.environ["PHOTON_PROJECT_ID"])
r = p.call("POST", "/webhooks/destinations",
           {"name": "primesight", "url": site + "/photon", "apiVersion": "2026-07-01", "enabledEvents": ["message.received"]},
           {"Idempotency-Key": "primesight-" + hashlib.sha256(site.encode()).hexdigest()[:24]})
env = os.path.join(ROOT, ".env")
lines = [l for l in open(env, encoding="utf-8").read().splitlines() if not l.startswith("PHOTON_WEBHOOK_SECRET=")]
open(env, "w", encoding="utf-8").write("\n".join(lines + [f"PHOTON_WEBHOOK_SECRET={r['signingSecret']}"]) + "\n")
print(f"webhook destination {r['destination'].get('id')} -> {site}/photon; signing secret saved to .env")
