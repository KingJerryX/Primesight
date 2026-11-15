"""Local server for Lane B (see LANES.md). Stdlib only.

python -m primesight.server            # http://localhost:8787
POST /brief     {"text", "mode"?}     -> {"briefing", "images", "property_id", "run_id"}  mode "draft": no LLM, ~4 s
POST /sms       {"phone", "text", "run_id"?} -> phone simulator; replies on GET /sms/<phone>?after=n. A page that sends its own
                                      run_id (12 hex) can follow GET /trace/<run_id> while the briefing is still being written
GET  /latest, /buildings, /incidents  -> newest run_id; ranked buildings; incident catalog
GET  /<page>.html                     -> demo pages (demo/ and demo/variants/)
POST /ask       {"run_id", "text"}      -> {"answer"}  crew reply, continues that run's agent thread
POST /feedback  {"property_id", "text"} -> {"ok": true}
GET  /trace/<run_id>                  -> JSON lines, one per step
GET  /run/<run_id>                    -> facts for the projector: floors, hydrants, staging, scan, SAM 3 counts
GET  /img/<name>                      -> floorplan, aerial and SAM 3 images (URLs from image_urls and /run)
PRIMESIGHT_REPLAY=1                   -> recorded calls answer instantly (see brief_or_replay)
"""
import hashlib, json, os, re, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from . import briefing, paths, store, trace

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


def more(th):
    """The answer to MORE in a crew thread: every gathered line the first text left out, from the same facts. No agent."""
    floor, incident = parse_call(th["call"])
    f = briefing.last.get(th["pid"])
    if not f or f["floor"] != floor:
        f = briefing.gather(th["pid"], floor)
    return briefing.compose(f, incident, classify(th["call"])["label"], full=True)


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
    sync_notes(pid)
    if os.environ.get("ANTHROPIC_API_KEY") and mode != "draft":
        from . import agent
        text_out = agent.run(text, run_id=run_id)
        f = briefing.last.get(pid)  # images from the agent's own gather call
        extra = {"texts": agent.OUTBOX.pop(run_id, []), "tenant_flags": agent.FLAGS.pop(run_id, [])}
    else:
        why = "no ANTHROPIC_API_KEY" if not os.environ.get("ANTHROPIC_API_KEY") else "no passcode or daily cap reached"
        trace.log(run_id, "mode", f"{why}: sending the draft briefing (no LLM)")
        trace.log(run_id, "gather_building", f"floor {floor}, {incident}")
        f = briefing.gather(pid, floor)
        text_out = briefing.compose(f, incident, classify(text)["label"])
        extra = {"texts": [], "tenant_flags": []}
    images = [p for p in ((f or {}).get("floorplan"), (f or {}).get("aerial")) if p]
    if store.remote:  # made on this instance's /tmp: other instances serve them from Supabase Storage (see GET /img)
        for p in images:
            if not os.path.exists(os.path.join(paths.ROOT, "output", os.path.basename(p))):
                try:
                    store.upload(p)
                except Exception as e:  # the text briefing still goes out; only the picture link may 404 elsewhere
                    trace.log(run_id, "error", f"image upload: {e}"[:200])
    trace.log(run_id, "done", f"{len(text_out.splitlines())} lines, {len(images)} images")
    return {"briefing": text_out, "images": images, "image_urls": [img_url(p) for p in images], "property_id": pid,
            **extra, "_facts": {**facts(f, incident), **extra, "call": text} if f else None}


VISION = os.path.join(paths.DATA, "cache", "vision")


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
        d = os.path.join(VISION, scan["id"])  # annotated faces stay local (not in git): a fresh clone has none
        imgs = sorted(os.listdir(d))[:12] if os.path.isdir(d) else []
        vis = {"scan_points": len(v["sweeps"]), "scan_points_with": v["sweeps_with"],
               "image_urls": [f"/img/vision/{scan['id']}/{n}" for n in imgs]}
    return {"incident": incident, "floor": fl,
            "building": {k: b[k] for k in ("name", "address", "stories", "year_built", "construction", "sprinklers")},
            "floors": [{"floor": n, "people": briefing._people(b["tenants"], {n}), "tenants": sorted(on.get(n, ())),
                        "scanned": n in scanned, "seen": seen.get(n)} for n in range(1, (b["stories"] or 0) + 1)],
            "hydrants": [{"m": m, "dir": d, "lat": la, "lon": lo} for m, d, la, lo in f["hydrants"][:5]],
            "staging": [{"m": m, "dir": d, "lat": la, "lon": lo, "score": s} for s, m, d, la, lo in f["staging"][:3]],
            "scan": {"floor": scan.get("floor"), "exact": f["scan_exact"], "link": scan.get("link")},
            "vision": vis}


DEMO = os.path.join(paths.DATA, "cache", "demo")  # recorded runs, keyed by call text
DEMO_PAGES = os.path.join(briefing.ROOT, "demo")  # console, projector and phone pages (and demo/variants/)


def buildings():
    """Every ranked building for the console's autocomplete: address, size, the scanned floor and whether SAM 3 ran."""
    path = os.path.join(paths.DATA, "cache", "ranked.json")
    out = []
    for r in json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []:
        cp = os.path.join(paths.DATA, "cache", f"costar_{r['pid']}.json")
        floors = sorted({t["floor"] for t in json.load(open(cp, encoding="utf-8"))["tenants"] if t["floor"]}) if os.path.exists(cp) else []
        out.append({**{k: r.get(k) for k in ("pid", "name", "address", "city", "lat", "lon", "type", "stories", "tenants", "scan_floor", "scan_name")},
                    "sam3": os.path.exists(os.path.join(VISION, f"{r['scan_id']}.json")), "tenant_floors": floors})
    return out
def run_facts(run_id):
    """Facts of a finished run (GET /run, /ask), or None. Kept in store so any instance can answer."""
    return (store.get("run:" + run_id) or {}).get("facts")


LIVE_CAP = int(os.environ.get("PRIMESIGHT_MAX_LIVE", 20))  # live agent runs per day on the public deploy (~$0.50 each)


def live_ok(passcode):
    """With PRIMESIGHT_PASSCODE set (the public deploy), a live agent run needs that passcode and fits under the daily cap.
    Everyone else still gets recorded runs, and the free draft briefing for any building."""
    want = os.environ.get("PRIMESIGHT_PASSCODE")
    if not want:
        return True
    if passcode != want:
        return False
    day = "live:" + time.strftime("%Y-%m-%d")
    n = store.get(day, 0)  # ponytail: read-then-write, two runs at once can both pass at the cap; fine for a demo
    if n >= LIVE_CAP:
        return False
    store.put(day, n + 1)
    return True


def sync_notes(pid):
    """Crew notes saved on other instances -> this instance's notes file, so the agent's /buildings/ view sees them."""
    if not store.remote or not pid:
        return
    shipped = os.path.join(paths.ROOT, "cache", "buildings", f"{int(pid)}.md")
    base = open(shipped, encoding="utf-8").read() if os.path.exists(shipped) else ""
    lines = "".join(n["line"] for n in store.read(f"notes:{int(pid)}"))
    if lines:
        d = os.path.join(paths.DATA, "cache", "buildings")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, f"{int(pid)}.md"), "w", encoding="utf-8").write(base + lines)


def brief_or_replay(text, run_id, mode=None, passcode=None):
    """Live runs are recorded. With PRIMESIGHT_REPLAY=1 a recorded call returns instantly, trace included
    (each trace line keeps its original t, so the projector can animate it). No passcode on the deploy: draft briefing."""
    store.put("latest", run_id)  # every projector follows the newest run (GET /latest)
    path = os.path.join(DEMO, hashlib.sha1(" ".join(text.lower().split()).encode()).hexdigest()[:16] + ".json")
    rec = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
    # A recording made before the short briefing format is stale: run it again (which records it afresh) instead of
    # replaying the old, longer text.
    if rec and not rec["out"].get("briefing", "").rstrip().endswith(briefing.MORE_HINT):
        rec = None
    if os.environ.get("PRIMESIGHT_REPLAY") and mode != "draft" and rec:
        if rec["out"].get("_facts") is None:  # recorded before /run existed: rebuild facts once (~3 s, no LLM)
            floor, incident = parse_call(text)
            rec["out"]["_facts"] = facts(briefing.gather(rec["out"]["property_id"], floor), incident)
            json.dump(rec, open(path, "w", encoding="utf-8"), indent=1)
        trace.write(run_id, rec["trace"])
        out = {**rec["out"], "replayed": True}
    else:
        if mode != "draft" and os.environ.get("ANTHROPIC_API_KEY") and not live_ok(passcode):
            mode = "draft"
        out = brief(text, run_id, mode)
        if out["property_id"] and mode != "draft" and os.environ.get("ANTHROPIC_API_KEY"):  # only agent runs become recordings
            os.makedirs(DEMO, exist_ok=True)
            json.dump({"text": text, "out": out, "trace": trace.read(run_id)}, open(path, "w", encoding="utf-8"), indent=1)
    f = out.pop("_facts", None)
    if f:
        f["call"] = text  # /ask needs it (older recordings didn't store it)
        f["briefing"] = out["briefing"]  # the projectors show what the phone got
        f["image_urls"] = out.get("image_urls") or [img_url(p) for p in out.get("images") or []]
        f.update(texts=out.get("texts") or [], tenant_flags=out.get("tenant_flags") or [])
        if "image_urls" not in out:
            out["image_urls"] = f["image_urls"]
    store.put("run:" + run_id, {"facts": f})
    return out


def ask_or_replay(run_id, call, text, passcode=None):
    """Crew reply. Recorded like /brief, keyed by call + reply text, so the demo can replay it."""
    path = os.path.join(DEMO, "ask_" + hashlib.sha1(" ".join(f"{call} || {text}".lower().split()).encode()).hexdigest()[:16] + ".json")
    trace.log(run_id, "crew_reply", text[:120])
    if os.environ.get("PRIMESIGHT_REPLAY") and os.path.exists(path):
        rec = json.load(open(path, encoding="utf-8"))
        trace.write(run_id, rec["trace"])
        return rec["answer"]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "PRIMESIGHT: follow-up questions need the agent (no API key on this server)."
    if not live_ok(passcode):
        return "PRIMESIGHT: follow-up questions run the live agent, which needs the judge passcode on this demo (or today's cap was reached)."
    from . import agent
    n = len(trace.read(run_id).splitlines())
    answer = agent.ask(run_id, text, call)
    new = "".join(line + "\n" for line in trace.read(run_id).splitlines()[n:])  # this reply's steps only
    os.makedirs(DEMO, exist_ok=True)
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
        if self.path == "/photon":  # Photon webhook: raw body, the signature covers the exact bytes
            from . import photon_bridge
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            code, msg = photon_bridge.webhook(self.headers, raw)
            return self._send(code, {"result": msg})
        body = self._json()
        if body is None:
            return self._send(400, {"error": "body must be JSON"})
        from . import console_api  # the game: /game/start, /game/answer, /game/name
        from . import drill  # the dispatcher round's segments: /drill/<segment>/...
        from . import review  # the team's review of the graders' ideas: /review/ (needs PRIMESIGHT_REVIEW_KEY)
        r = console_api.post(self.path, body) or drill.post(self.path, body) or review.post(self.path, body)
        if r:
            return self._send(*r)
        if self.path == "/brief":
            if not str(body.get("text", "")).strip():
                return self._send(400, {"error": "text is required"})
            run_id = uuid.uuid4().hex[:12]
            try:
                out = brief_or_replay(body["text"], run_id, body.get("mode"), body.get("passcode"))
            except Exception as e:  # the crew still needs *something*
                trace.log(run_id, "error", repr(e)[:200])
                out = {"briefing": f"PRIMESIGHT: briefing failed ({type(e).__name__}). Proceed on standard procedure.",
                       "images": [], "property_id": None}
            return self._send(200, {**out, "run_id": run_id})
        if self.path == "/ask":
            run_id = str(body.get("run_id", ""))
            f = run_facts(run_id) if re.fullmatch(r"[0-9a-f]{12}", run_id) else None
            if not f or not str(body.get("text", "")).strip():
                return self._send(400, {"error": "run_id of a run on this server and text are required"})
            return self._send(200, {"answer": ask_or_replay(run_id, f["call"], body["text"], body.get("passcode"))})
        if self.path == "/sms":  # phone simulator: {"phone": "sim-1", "text"}; replies arrive on GET /sms/<phone>
            phone = str(body.get("phone", ""))
            if not re.fullmatch(r"[\w-]{1,32}", phone) or not str(body.get("text", "")).strip():
                return self._send(400, {"error": "phone (a simulator id like sim-1, never a real number) and text are required"})
            from . import sms
            run_id = body.get("run_id") if re.fullmatch(r"[0-9a-f]{12}", str(body.get("run_id", ""))) else None
            # The first responder console sends as "judge". With PRIMESIGHT_CREW_PHONE in .env that is a real phone:
            # the dispatch and its briefing go there through Photon instead of to the simulator page.
            crew = os.environ.get("PRIMESIGHT_CREW_PHONE") if phone == "judge" else None
            if crew:
                from . import photon_bridge
                try:
                    return self._send(200, {"ok": True, "real": True, "phone": photon_bridge.push(crew, body["text"], run_id)})
                except RuntimeError as e:
                    return self._send(409, {"error": str(e)})
            sms.simulate(phone, body["text"], body.get("passcode"), run_id)
            return self._send(200, {"ok": True})
        if self.path == "/feedback":
            if not body.get("property_id") or not str(body.get("text", "")).strip():
                return self._send(400, {"error": "property_id and text are required"})
            add_note(body["property_id"], body["text"])
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})

    def do_GET(self):
        from . import console_api, drill  # Lane B's console: /scans/<pid>, /aerial/<pid>/<view>.jpg; the dispatcher round: /drill/
        r = console_api.handle(self.path) or drill.handle(self.path)
        if r:
            return self._send(*r)
        m = re.fullmatch(r"/trace/([0-9a-f]{12})", self.path)
        if m:
            return self._send(200, trace.read(m.group(1)).encode(), "application/x-ndjson")
        m = re.fullmatch(r"/run/([0-9a-f]{12})", self.path)
        if m:
            f = run_facts(m.group(1))
            return self._send(200, f) if f else self._send(404, {"error": "unknown run (server restarted?)"})
        m = re.fullmatch(r"/img/(vision/\w+/\d{3}_\d\.jpg|[\w-]+\.jpg)", self.path)  # strict: no path traversal
        if m:
            p = os.path.join(VISION, *m.group(1).split("/")[1:]) if m.group(1).startswith("vision/") \
                else os.path.join(briefing.OUT, m.group(1))
            if os.path.exists(p):
                return self._send(200, open(p, "rb").read(), "image/jpeg")
            if store.remote and not m.group(1).startswith("vision/"):  # made on another instance: it uploaded it
                self.send_response(302)
                self.send_header("Location", store.public(m.group(1)))
                self.send_header("Access-Control-Allow-Origin", "*")
                return self.end_headers()
            return self._send(404, {"error": "no image"})
        m = re.fullmatch(r"/sms/([\w-]{1,32})(?:\?after=(\d+))?", self.path)
        if m:
            from . import sms
            return self._send(200, sms.messages(m.group(1), int(m.group(2) or 0)))
        if self.path == "/latest":
            rid = store.get("latest")
            return self._send(200, {"run_id": rid, "done": bool(rid) and store.get("run:" + rid) is not None})
        if self.path == "/buildings":
            return self._send(200, buildings())
        if self.path == "/incidents":
            return self._send(200, [{k: t[k] for k in ("id", "label", "category", "needs", "layers", "keywords")} for t in CATALOG])
        page = self.path.split("?", 1)[0]  # pages read their own query (phone.html?id=judge, projector-*.html?speed=3)
        m = re.fullmatch(r"/(?:demo/)?([\w-]+\.(html|css|js|json))", page) or re.fullmatch(r"/()", page)
        if m:  # the demo pages, served from demo/ (strict names, no subfolders except variants)
            name = m.group(1) or "home.html"  # / is the public homepage; the demo hub stays at /index.html
            p = os.path.join(DEMO_PAGES, name) if os.path.exists(os.path.join(DEMO_PAGES, name)) else os.path.join(DEMO_PAGES, "variants", name)
            ctype = {"html": "text/html", "css": "text/css", "js": "application/javascript", "json": "application/json"}[name.rsplit(".", 1)[1]]
            return self._send(200, open(p, "rb").read(), ctype + "; charset=utf-8") if os.path.exists(p) else self._send(404, {"error": "no page"})
        if self.path == "/health":
            return self._send(200, {"ok": True, "agent": bool(os.environ.get("ANTHROPIC_API_KEY")), "photon": bool(os.environ.get("PHOTON_API_KEY") and os.environ.get("PHOTON_PROJECT_ID")),
                                    "store": "supabase" if store.remote else "memory", "passcode_required": bool(os.environ.get("PRIMESIGHT_PASSCODE")),
                                    "webhook_secret": bool(os.environ.get("PHOTON_WEBHOOK_SECRET"))})
        self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    assert parse_call("Cardiac arrest, 36th floor, 1000 Louisiana") == (36, "medical")
    assert parse_call("Smoke on floor 30") == (30, "fire")
    assert classify("Man collapsed in the break room")["id"] == "unconscious" and classify("smoke alarm going off")["id"] == "fire_alarm"
    assert parse_call("Two people stuck in elevator, 1000 Louisiana") == (1, "rescue") and classify("Shots fired, 12th floor")["category"] == "security"
    for call, want in [("Electrical fire on the 5th floor", "electrical_fire"), ("Solar panel fire on the roof", "solar_fire"),
                       ("E-bike battery caught fire in the lobby", "battery_fire"), ("Car into the lobby, drove into the glass", "vehicle_into_building"),
                       ("Worker with heat stroke on the roof", "heat_illness"), ("Stroke symptoms, 11th floor", "stroke"),
                       ("Strange odor on the 9th floor, people feel sick", "odor_unknown"), ("Strong gas odor, 20th floor", "gas_leak"),
                       ("Smoke coming into the building from outside", "smoke"), ("Patient is sick on floor 3", "medical_other"),
                       ("Something happened", "medical_other"), ("EV battery fire in the parking garage", "parking_garage_fire"),
                       ("Car fire on parking level 2", "parking_garage_fire"), ("Car crashed into the garage entrance", "vehicle_into_building")]:
        assert classify(call)["id"] == want, (call, classify(call)["id"], want)
    print(f"Primesight server on http://localhost:{PORT}  (agent: {'on' if os.environ.get('ANTHROPIC_API_KEY') else 'off, sending drafts'})")
    from . import photon_bridge  # real iMessage/SMS through Photon when PHOTON_API_KEY + PHOTON_PROJECT_ID are in .env
    print(f"Photon bridge: {'polling' if photon_bridge.start_from_env() else 'off (no PHOTON_API_KEY / PHOTON_PROJECT_ID); phone.html still works'}")
    # PRIMESIGHT_HOST=0.0.0.0 lets a phone on the same Wi-Fi open the pages (Windows may ask to allow Python through the firewall)
    ThreadingHTTPServer((os.environ.get("PRIMESIGHT_HOST", "127.0.0.1"), PORT), H).serve_forever()
