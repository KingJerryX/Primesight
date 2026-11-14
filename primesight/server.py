"""Local server for Lane B (see LANES.md). Stdlib only.

python -m primesight.server            # http://localhost:8787
POST /brief     {"text", "mode"?}     -> {"briefing", "images", "property_id", "run_id"}  mode "draft": no LLM, ~4 s
POST /sms       {"phone", "text"}     -> phone simulator; replies on GET /sms/<phone>?after=n
GET  /latest, /buildings, /incidents  -> newest run_id; ranked buildings; incident catalog
GET  /<page>.html                     -> demo pages (demo/ and demo/variants/)
POST /ask       {"run_id", "text"}      -> {"answer"}  crew reply, continues that run's agent thread
POST /feedback  {"property_id", "text"} -> {"ok": true}
GET  /trace/<run_id>                  -> JSON lines, one per step
GET  /run/<run_id>                    -> facts for the projector: floors, hydrants, staging, scan, SAM 3 counts
GET  /img/<name>                      -> floorplan, aerial and SAM 3 images (URLs from image_urls and /run)
PRIMESIGHT_REPLAY=1                   -> recorded calls answer instantly (see brief_or_replay)
"""
import hashlib, json, os, re, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from . import briefing, trace

PORT = int(os.environ.get("PRIMESIGHT_PORT", 8787))


CATALOG = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "agentfs", "incidents.json"),
                         encoding="utf-8"))["types"]


def classify(text):
    """Incident type from the catalog: first type with a keyword in the text (specific types come first)."""
    t = " ".join(text.lower().split()) + " "
    return next((ty for ty in CATALOG if any(k in t for k in ty["keywords"])), CATALOG[-1])


def parse_call(text):
    """Cheap parse used when the agent can't run (no API key): floor, incident category."""
    t = text.lower()
    m = re.search(r"(\d+)(?:st|nd|rd|th)?\s*(?:floor|fl\b)", t) or re.search(r"floor\s*(\d+)", t)
    return (int(m.group(1)) if m else 1), classify(text)["category"]


def brief(text, run_id, mode=None):
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
    if os.environ.get("ANTHROPIC_API_KEY") and mode != "draft":
        from . import agent
        text_out = agent.run(text, run_id=run_id)
        f = briefing.last.get(pid)  # images from the agent's own gather call
        extra = {"texts": agent.OUTBOX.pop(run_id, []), "tenant_flags": agent.FLAGS.pop(run_id, [])}
    else:
        trace.log(run_id, "mode", "no ANTHROPIC_API_KEY: sending the draft briefing")
        trace.log(run_id, "gather_building", f"floor {floor}, {incident}")
        f = briefing.gather(pid, floor)
        text_out = briefing.compose(f, incident, classify(text)["label"])
        extra = {"texts": [], "tenant_flags": []}
    images = [p for p in ((f or {}).get("floorplan"), (f or {}).get("aerial")) if p]
    trace.log(run_id, "done", f"{len(text_out.splitlines())} lines, {len(images)} images")
    return {"briefing": text_out, "images": images, "image_urls": [img_url(p) for p in images], "property_id": pid,
            **extra, "_facts": {**facts(f, incident), **extra, "call": text} if f else None}


VISION = os.path.join(briefing.ROOT, "cache", "vision")


def img_url(path):
    """Local image path -> URL under /img/ (output/<name>.jpg or cache/vision/<scan>/<name>.jpg)."""
    rel = os.path.relpath(path, VISION) if path.startswith(VISION) else os.path.basename(path)
    return "/img/" + ("vision/" + rel.replace(os.sep, "/") if path.startswith(VISION) else rel)


def facts(f, incident):
    """Structured facts for the projector page: the building floor by floor, outside points, scan and SAM 3 results."""
    b, fl, scan = f["building"], f["floor"], f.get("scan") or {}
    scanned = {s["floor"] for s in f.get("scans") or [] if s["floor"] is not None}
    seen = {}  # floor -> SAM 3 counts, for every scanned floor the GPU batch has finished
    for s in f.get("scans") or []:
        p = os.path.join(VISION, f"{s['id']}.json")
        if s["floor"] is not None and os.path.exists(p):
            v = json.load(open(p, encoding="utf-8"))
            seen[s["floor"]] = {"scan_points": len(v["sweeps"]), **v["sweeps_with"]}
    on = {}
    for t in b["tenants"]:
        on.setdefault(t["floor"], set()).add(t["name"])
    vis, vpath = None, os.path.join(VISION, f"{scan.get('id')}.json")
    if scan and os.path.exists(vpath):
        v = json.load(open(vpath, encoding="utf-8"))
        imgs = sorted(os.listdir(os.path.join(VISION, scan["id"])))[:12]
        vis = {"scan_points": len(v["sweeps"]), "scan_points_with": v["sweeps_with"],
               "image_urls": [f"/img/vision/{scan['id']}/{n}" for n in imgs]}
    return {"incident": incident, "floor": fl,
            "building": {k: b[k] for k in ("name", "address", "stories", "year_built", "construction", "sprinklers")},
            "floors": [{"floor": n, "people": briefing._people(b["tenants"], {n}), "tenants": sorted(on.get(n, ())),
                        "scanned": n in scanned, "seen": seen.get(n)} for n in range(1, (b["stories"] or 0) + 1)],
            "hydrants": [{"m": m, "dir": d, "lat": la, "lon": lo} for m, d, la, lo in f["hydrants"][:5]],
            "staging": [{"m": m, "dir": d, "lat": la, "lon": lo, "score": s} for s, m, d, la, lo in f["staging"][:3]],
            "scan": {"floor": scan.get("floor"), "exact": f["scan_exact"], "link": scan.get("link")},
            "vision": vis, "walk": briefing.walk_line(r) if (r := (f.get("vision") or {}).get("route")) else None}


DEMO = os.path.join(briefing.ROOT, "cache", "demo")  # recorded runs, keyed by call text
DEMO_PAGES = os.path.join(briefing.ROOT, "demo")  # console, projector and phone pages (and demo/variants/)


def buildings():
    """Every ranked building for the console's autocomplete: address, size, the scanned floor and whether SAM 3 ran."""
    path = os.path.join(briefing.ROOT, "cache", "ranked.json")
    out = []
    for r in json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []:
        cp = os.path.join(briefing.ROOT, "cache", f"costar_{r['pid']}.json")
        floors = sorted({t["floor"] for t in json.load(open(cp, encoding="utf-8"))["tenants"] if t["floor"]}) if os.path.exists(cp) else []
        out.append({**{k: r.get(k) for k in ("pid", "name", "address", "city", "lat", "lon", "type", "stories", "tenants", "scan_floor", "scan_name")},
                    "sam3": os.path.exists(os.path.join(VISION, f"{r['scan_id']}.json")), "tenant_floors": floors})
    return out
RUNS = {}  # run_id -> facts, for GET /run/<run_id>
STARTED = []  # run_ids in start order, so every projector can follow the newest run (GET /latest)


def brief_or_replay(text, run_id, mode=None):
    """Live runs are recorded. With PRIMESIGHT_REPLAY=1 a recorded call returns instantly, trace included
    (each trace line keeps its original t, so the projector can animate it)."""
    STARTED.append(run_id)
    path = os.path.join(DEMO, hashlib.sha1(" ".join(text.lower().split()).encode()).hexdigest()[:16] + ".json")
    if os.environ.get("PRIMESIGHT_REPLAY") and mode != "draft" and os.path.exists(path):
        rec = json.load(open(path, encoding="utf-8"))
        if rec["out"].get("_facts") is None:  # recorded before /run existed: rebuild facts once (~3 s, no LLM)
            floor, incident = parse_call(text)
            rec["out"]["_facts"] = facts(briefing.gather(rec["out"]["property_id"], floor), incident)
            json.dump(rec, open(path, "w", encoding="utf-8"), indent=1)
        trace.write(run_id, rec["trace"])
        out = {**rec["out"], "replayed": True}
    else:
        out = brief(text, run_id, mode)
        if out["property_id"] and mode != "draft":  # only agent runs become recordings
            os.makedirs(DEMO, exist_ok=True)
            json.dump({"text": text, "out": out, "trace": trace.read(run_id)}, open(path, "w", encoding="utf-8"), indent=1)
    RUNS[run_id] = out.pop("_facts", None)
    if RUNS[run_id]:
        RUNS[run_id]["call"] = text  # /ask needs it (older recordings didn't store it)
        RUNS[run_id]["briefing"] = out["briefing"]  # the projectors show what the phone got
        RUNS[run_id]["image_urls"] = out.get("image_urls") or [img_url(p) for p in out.get("images") or []]
        RUNS[run_id].update(texts=out.get("texts") or [], tenant_flags=out.get("tenant_flags") or [])
    if RUNS[run_id] and "image_urls" not in out:
        out["image_urls"] = [img_url(p) for p in out["images"]]
    return out


def ask_or_replay(run_id, call, text):
    """Crew reply. Recorded like /brief, keyed by call + reply text, so the demo can replay it."""
    path = os.path.join(DEMO, "ask_" + hashlib.sha1(" ".join(f"{call} || {text}".lower().split()).encode()).hexdigest()[:16] + ".json")
    trace.log(run_id, "crew_reply", text[:120])
    if os.environ.get("PRIMESIGHT_REPLAY") and os.path.exists(path):
        rec = json.load(open(path, encoding="utf-8"))
        trace.write(run_id, rec["trace"])
        return rec["answer"]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "PRIMESIGHT: follow-up questions need the agent (no API key on this server)."
    from . import agent
    n = len(trace.read(run_id).splitlines())
    answer = agent.ask(run_id, text, call)
    new = "".join(line + "\n" for line in trace.read(run_id).splitlines()[n:])  # this reply's steps only
    json.dump({"call": call, "text": text, "answer": answer, "trace": new}, open(path, "w", encoding="utf-8"), indent=1)
    return answer


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
                out = brief_or_replay(body["text"], run_id, body.get("mode"))
            except Exception as e:  # the crew still needs *something*
                trace.log(run_id, "error", repr(e)[:200])
                out = {"briefing": f"PRIMESIGHT: briefing failed ({type(e).__name__}). Proceed on standard procedure.",
                       "images": [], "property_id": None}
            return self._send(200, {**out, "run_id": run_id})
        if self.path == "/ask":
            run_id, f = str(body.get("run_id", "")), RUNS.get(str(body.get("run_id", "")))
            if not f or not str(body.get("text", "")).strip():
                return self._send(400, {"error": "run_id of a run on this server and text are required"})
            return self._send(200, {"answer": ask_or_replay(run_id, f["call"], body["text"])})
        if self.path == "/sms":  # phone simulator: {"phone": "sim-1", "text"}; replies arrive on GET /sms/<phone>
            phone = str(body.get("phone", ""))
            if not re.fullmatch(r"[\w-]{1,32}", phone) or not str(body.get("text", "")).strip():
                return self._send(400, {"error": "phone (a simulator id like sim-1, never a real number) and text are required"})
            from . import sms
            sms.simulate(phone, body["text"])
            return self._send(200, {"ok": True})
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
        m = re.fullmatch(r"/run/([0-9a-f]{12})", self.path)
        if m:
            f = RUNS.get(m.group(1))
            return self._send(200, f) if f else self._send(404, {"error": "unknown run (server restarted?)"})
        m = re.fullmatch(r"/img/(vision/\w+/\d{3}_\d\.jpg|[\w-]+\.jpg)", self.path)  # strict: no path traversal
        if m:
            p = os.path.join(VISION, *m.group(1).split("/")[1:]) if m.group(1).startswith("vision/") \
                else os.path.join(briefing.OUT, m.group(1))
            return self._send(200, open(p, "rb").read(), "image/jpeg") if os.path.exists(p) else self._send(404, {"error": "no image"})
        m = re.fullmatch(r"/sms/([\w-]{1,32})(?:\?after=(\d+))?", self.path)
        if m:
            from . import sms
            return self._send(200, sms.LOG.get(m.group(1), [])[int(m.group(2) or 0):])
        if self.path == "/latest":
            return self._send(200, {"run_id": STARTED[-1] if STARTED else None, "done": bool(STARTED) and STARTED[-1] in RUNS})
        if self.path == "/buildings":
            return self._send(200, buildings())
        if self.path == "/incidents":
            return self._send(200, [{k: t[k] for k in ("id", "label", "category", "needs", "layers", "keywords")} for t in CATALOG])
        page = self.path.split("?", 1)[0]  # pages read their own query (phone.html?id=judge, projector-*.html?speed=3)
        m = re.fullmatch(r"/(?:demo/)?([\w-]+\.(html|css|js|json))", page) or re.fullmatch(r"/()", page)
        if m:  # the demo pages, served from demo/ (strict names, no subfolders except variants)
            name = m.group(1) or "index.html"
            p = os.path.join(DEMO_PAGES, name) if os.path.exists(os.path.join(DEMO_PAGES, name)) else os.path.join(DEMO_PAGES, "variants", name)
            ctype = {"html": "text/html", "css": "text/css", "js": "application/javascript", "json": "application/json"}[name.rsplit(".", 1)[1]]
            return self._send(200, open(p, "rb").read(), ctype + "; charset=utf-8") if os.path.exists(p) else self._send(404, {"error": "no page"})
        if self.path == "/health":
            return self._send(200, {"ok": True, "agent": bool(os.environ.get("ANTHROPIC_API_KEY"))})
        self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    assert parse_call("Cardiac arrest, 36th floor, 1000 Louisiana") == (36, "medical")
    assert parse_call("Smoke on floor 30") == (30, "fire")
    assert classify("Man collapsed in the break room")["id"] == "unconscious" and classify("smoke alarm going off")["id"] == "fire_alarm"
    assert parse_call("Two people stuck in elevator, 1000 Louisiana") == (1, "rescue") and classify("Shots fired, 12th floor")["category"] == "security"
    print(f"Primesight server on http://localhost:{PORT}  (agent: {'on' if os.environ.get('ANTHROPIC_API_KEY') else 'off, sending drafts'})")
    # PRIMESIGHT_HOST=0.0.0.0 lets a phone on the same Wi-Fi open the pages (Windows may ask to allow Python through the firewall)
    ThreadingHTTPServer((os.environ.get("PRIMESIGHT_HOST", "127.0.0.1"), PORT), H).serve_forever()
