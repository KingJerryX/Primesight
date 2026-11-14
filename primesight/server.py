"""Local server for Lane B (see LANES.md). Stdlib only.

python -m primesight.server            # http://localhost:8787
POST /brief     {"text", "thread"?}   -> {"briefing", "images", "property_id", "run_id"}
POST /feedback  {"property_id", "text"} -> {"ok": true}
GET  /trace/<run_id>                  -> JSON lines, one per step
"""
import json, os, re, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from . import briefing, trace

PORT = int(os.environ.get("PRIMESIGHT_PORT", 8787))


def parse_call(text):
    """Cheap parse used when the agent can't run (no API key): floor, incident type."""
    t = text.lower()
    m = re.search(r"(\d+)(?:st|nd|rd|th)?\s*(?:floor|fl\b)", t) or re.search(r"floor\s*(\d+)", t)
    floor = int(m.group(1)) if m else 1
    incident = ("fire" if re.search(r"fire|smoke|alarm|burn", t) else
                "hazmat" if re.search(r"hazmat|chemical|gas leak|spill|fumes", t) else "medical")
    return floor, incident


def brief(text, run_id):
    """Agent when a key is set; otherwise the deterministic draft. Same output shape either way."""
    from .agent import find_building
    trace.log(run_id, "call_received", text[:120])
    hits = json.loads(find_building(text))
    if not hits:
        trace.log(run_id, "address_lookup", "no match")
        return {"briefing": "PRIMESIGHT: address not found in building data. Send the street address.", "images": [], "property_id": None}
    pid = hits[0]["property_id"]
    trace.log(run_id, "address_lookup", f"{hits[0]['address']}, {hits[0]['city']} -> {pid}")
    floor, incident = parse_call(text)
    if os.environ.get("ANTHROPIC_API_KEY"):
        from . import agent
        text_out = agent.run(text, run_id=run_id)
        f = briefing.last.get(pid)  # images from the agent's own gather call
    else:
        trace.log(run_id, "mode", "no ANTHROPIC_API_KEY: sending the draft briefing")
        trace.log(run_id, "gather_building", f"floor {floor}, {incident}")
        f = briefing.gather(pid, floor)
        text_out = briefing.compose(f, incident)
    images = [p for p in ((f or {}).get("floorplan_marked") or (f or {}).get("floorplan"), (f or {}).get("aerial")) if p]
    trace.log(run_id, "done", f"{len(text_out.splitlines())} lines, {len(images)} images")
    return {"briefing": text_out, "images": images, "property_id": pid}


def add_note(pid, text):
    from .agent import add_building_note
    add_building_note(pid, f"crew: {text}")


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")  # Lane B's pages run from file:// or another port
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, b"")

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return None

    def do_POST(self):
        body = self._json()
        if body is None:
            return self._send(400, {"error": "body must be JSON"})
        if self.path == "/brief":
            if not str(body.get("text", "")).strip():
                return self._send(400, {"error": "text is required"})
            run_id = uuid.uuid4().hex[:12]
            try:
                out = brief(body["text"], run_id)
            except Exception as e:  # the crew still needs *something*
                trace.log(run_id, "error", repr(e)[:200])
                out = {"briefing": f"PRIMESIGHT: briefing failed ({type(e).__name__}). Proceed on standard procedure.",
                       "images": [], "property_id": None}
            return self._send(200, {**out, "run_id": run_id})
        if self.path == "/feedback":
            if not body.get("property_id") or not str(body.get("text", "")).strip():
                return self._send(400, {"error": "property_id and text are required"})
            add_note(body["property_id"], body["text"])
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})

    def do_GET(self):
        m = re.fullmatch(r"/trace/([0-9a-f]{12})", self.path)
        if m:
            return self._send(200, trace.read(m.group(1)).encode(), "application/x-ndjson")
        if self.path == "/health":
            return self._send(200, {"ok": True, "agent": bool(os.environ.get("ANTHROPIC_API_KEY"))})
        self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    assert parse_call("Cardiac arrest, 36th floor, 1000 Louisiana") == (36, "medical")
    assert parse_call("Smoke on floor 30") == (30, "fire")
    print(f"Primesight server on http://localhost:{PORT}  (agent: {'on' if os.environ.get('ANTHROPIC_API_KEY') else 'off, sending drafts'})")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
