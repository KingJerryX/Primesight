"""Photon 2.0 bridge: real iMessage/SMS in and out, through the same conversation code as the phone simulator (sms.handle).

Poll  GET  /v1/projects/{id}/messages?direction=received&order=asc&createdSince=...   every 2 s (Photon's quickstart pattern)
Reply POST /v1/projects/{id}/messages {spaceId: m.space.id, senderId: m.receivedBy.id, content}   + idempotency-key
Image POST /v1/projects/{id}/attachments (multipart/related: JSON part, then the bytes), then send {type: attachment}
Typing POST /v1/projects/{id}/spaces/{spaceId}/typing {senderId, typing} while the agent works

Env (.env): PHOTON_API_KEY, PHOTON_PROJECT_ID, optional PHOTON_PLATFORM (unset = every platform: the filter can hide texts),
PHOTON_API (defaults to https://api.photon.codes; the self-test points it at a local mock).

  python -m primesight.photon_bridge register <your handle>   shared-line assignment; prints the line to text START to
  python -m primesight.photon_bridge selftest                 full round trip against a local mock, no network
The server starts the bridge itself when PHOTON_API_KEY and PHOTON_PROJECT_ID are set, so projectors see Photon runs too.

Privacy: a crew's handle never goes in a log, trace or URL; conversations are keyed by "ph-" + a hash of it.
The shared line cannot text first: a phone must text it (START) before Primesight can answer.
"""
import hashlib, json, mimetypes, os, sys, threading, time, urllib.error, urllib.parse, urllib.request, uuid
from datetime import datetime, timedelta, timezone

from . import sms, store

API = os.environ.get("PHOTON_API", "https://api.photon.codes")


def key_for(handle):
    return "ph-" + hashlib.sha256(handle.encode()).hexdigest()[:12]


class Photon:
    def __init__(self, api_key, project_id, api=API):
        self.key, self.pid, self.api = api_key, project_id, api.rstrip("/")

    def call(self, method, path, body=None, headers=None, raw=None):
        # Cloudflare in front of api.photon.codes blocks urllib's default UA with 403 "error code: 1010"
        h = {"Authorization": f"Bearer {self.key}", "User-Agent": "primesight-photon-bridge/1.0", **(headers or {})}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if body is not None and raw is None:
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.api}/v1/projects/{self.pid}{path}", data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                txt = r.read().decode()
                return json.loads(txt) if txt else {}
        except urllib.error.HTTPError as e:  # Photon's error body says what was wrong: keep it
            raise RuntimeError(f"Photon {method} {path.split('?')[0]} -> {e.code}: {e.read().decode()[:300]}") from None

    def received(self, since, platform=None):
        """Every received message created at or after `since`, oldest first, across pages."""
        out, token = [], None
        while True:
            q = {"direction": "received", "order": "asc", "pageSize": 100, "createdSince": since}
            if platform:
                q["platform"] = platform
            if token:
                q["pageToken"] = token
            page = self.call("GET", "/messages?" + urllib.parse.urlencode(q))
            out += page.get("items", [])
            token = page.get("nextPageToken")
            if not token:
                return out

    def send_text(self, space, sender, text, parent=None):
        return self.call("POST", "/messages", {"spaceId": space, "senderId": sender, **({"parentMessageId": parent} if parent else {}),
                                               "content": {"type": "text", "format": "plain", "text": text}},
                         {"idempotency-key": uuid.uuid4().hex})

    def upload(self, path):
        data = open(path, "rb").read()
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        meta = json.dumps({"contentType": ctype, "sizeBytes": len(data), "filename": os.path.basename(path)}).encode()
        b = uuid.uuid4().hex
        raw = (f"--{b}\r\nContent-Type: application/json\r\n\r\n".encode() + meta +
               f"\r\n--{b}\r\nContent-Type: {ctype}\r\n\r\n".encode() + data + f"\r\n--{b}--\r\n".encode())
        att = self.call("POST", "/attachments", headers={"Content-Type": f"multipart/related; boundary={b}",
                                                         "idempotency-key": hashlib.sha256(data).hexdigest()[:40]}, raw=raw)
        return att["id"]

    def send_image(self, space, sender, path):
        return self.call("POST", "/messages", {"spaceId": space, "senderId": sender,
                                               "content": {"type": "attachment", "attachmentId": self.upload(path)}},
                         {"idempotency-key": uuid.uuid4().hex})

    def typing(self, space, sender, on):
        try:
            self.call("POST", f"/spaces/{space}/typing", {"senderId": sender, "typing": on})
        except Exception:
            pass  # typing is a nicety; never let it block a briefing

    def register(self, handle):
        return self.call("POST", "/platforms/imessage/assignments", {"handle": handle},
                         {"Idempotency-Key": "primesight-" + key_for(handle)})


class Bridge:
    def __init__(self, photon, platform=None):
        self.p, self.platform = photon, platform
        # Start a minute back so a text sent while the server restarted still gets answered, but not old history.
        self.since = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.seen, self.errors = set(), 0

    def poll_once(self):
        items = self.p.received(self.since, self.platform)
        for m in items:
            if m["id"] in self.seen:
                continue
            self.seen.add(m["id"])
            args = incoming(m)
            if args and (not store.remote or store.claim("photon:" + m["id"])):  # a deployed webhook may answer it first
                threading.Thread(target=self.answer, args=args, daemon=True).start()
        if items:  # move the window only after paging finished; inclusive bound, `seen` dedupes
            self.since = items[-1]["createdAt"]
        return len(items)

    def answer(self, handle, space, sender, text, parent=None):
        phone = key_for(handle)
        sms.log(phone, "crew", {"type": "text", "text": text})  # mirrored on phone.html?id=ph-..., handle never shown

        def send(msg):
            sms.log(phone, "primesight", msg)
            try:
                if msg["type"] == "image" and msg.get("path") and os.path.exists(msg["path"]):
                    self.p.send_image(space, sender, msg["path"])
                elif msg["type"] == "text":
                    self.p.send_text(space, sender, msg["text"], parent)  # threads the reply under the crew's text
            except Exception as e:
                print(f"[photon] send failed for {phone}: {e}", file=sys.stderr)
                if msg["type"] == "image":  # an image that won't upload becomes a link the crew can still open
                    try:
                        self.p.send_text(space, sender, f"{msg.get('caption', 'Image')}: {public_url(msg.get('url'))}")
                    except Exception:
                        pass
        self.p.typing(space, sender, True)
        try:
            sms.handle(phone, text, send, os.environ.get("PRIMESIGHT_PASSCODE"))  # registered phones may run the agent (daily cap still applies)
        finally:
            self.p.typing(space, sender, False)

    def run(self, every=2.0):
        while True:
            try:
                self.poll_once()
                self.errors = 0
            except Exception as e:  # network blips: back off, keep going
                self.errors += 1
                print(f"[photon] poll failed ({self.errors}): {e}", file=sys.stderr)
            time.sleep(min(every * (1 + self.errors), 30))


def incoming(m):
    """A received 1:1 text -> (handle, space_id, sender_id, text, message_id); anything else (groups, media) -> None."""
    sent, space, recv = m.get("sentBy") or {}, m.get("space") or {}, m.get("receivedBy") or {}
    text = (m.get("content") or {}).get("text") if (m.get("content") or {}).get("type") == "text" else None
    ok = (m.get("status") == "received" and text and sent.get("handle") and space.get("id") and recv.get("id")
          and "group" not in str(space.get("kind", "")).lower())
    return (sent["handle"], space["id"], recv["id"], text, m["id"]) if ok else None


def verify(headers, raw, secret):
    """Standard Webhooks signature (whsec_ secrets): v1,base64(HMAC-SHA256(key, "<id>.<timestamp>.<body>")), 5 min window."""
    import base64, hmac
    mid, ts, sig = headers.get("webhook-id"), headers.get("webhook-timestamp"), headers.get("webhook-signature") or ""
    if not (mid and ts and secret) or not ts.isdigit() or abs(time.time() - int(ts)) > 300:
        return False
    key = base64.b64decode(secret.removeprefix("whsec_"))
    want = base64.b64encode(hmac.new(key, f"{mid}.{ts}.".encode() + raw, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(want, part.split(",", 1)[-1]) for part in sig.split())


def webhook(headers, raw):
    """POST /photon on the deploy: a signed message.received event -> the same answer path as polling, inside the request
    (serverless has no background). Retries and the laptop poller are deduped by message id. Returns (status, note)."""
    if not verify(headers, raw, os.environ.get("PHOTON_WEBHOOK_SECRET", "")):
        print(f"[photon] webhook rejected; signature headers present: {[h for h in headers.keys() if 'webhook' in h.lower() or 'signature' in h.lower()]}", file=sys.stderr)
        return 401, "bad signature"
    ev = json.loads(raw)
    m = (ev.get("data") or {}).get("object") or {}
    args = incoming(m) if ev.get("type") == "message.received" else None
    if not args:
        return 200, "ignored"
    if not store.claim("photon:" + m["id"]):
        return 200, "duplicate"
    k, pid = os.environ.get("PHOTON_API_KEY"), os.environ.get("PHOTON_PROJECT_ID")
    Bridge(Photon(k, pid, os.environ.get("PHOTON_API", API))).answer(*args)
    return 200, "answered"


def public_url(path):
    return (os.environ.get("PRIMESIGHT_PUBLIC_URL", "").rstrip("/") + (path or "")) or "(image unavailable)"


def start_from_env():
    """Called by the server at startup. Returns the Bridge, or None when Photon isn't configured."""
    k, pid = os.environ.get("PHOTON_API_KEY"), os.environ.get("PHOTON_PROJECT_ID")
    if not (k and pid):
        return None
    b = Bridge(Photon(k, pid, os.environ.get("PHOTON_API", API)), os.environ.get("PHOTON_PLATFORM") or None)
    threading.Thread(target=b.run, daemon=True).start()
    print("[photon] bridge polling", file=sys.stderr)
    return b


def selftest():
    """Round trip against a local mock of the four Photon endpoints: HELP text in -> HELP text out, image upload."""
    store.remote = False  # never write test rows into the deployed Supabase, even with its keys in .env
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    got = {"messages": [], "attachments": [], "typing": [], "auth": set()}
    inbox = [{"id": "pho_msg_1", "status": "received", "platform": "imessage", "createdAt": "2026-11-14T08:00:00.000Z",
              "content": {"type": "text", "format": "plain", "text": "help"},
              "sentBy": {"id": "pho_usr_crew", "kind": "user", "handle": "sim-crew-handle"},
              "receivedBy": {"id": "pho_usr_line", "kind": "agent"}, "space": {"id": "pho_spc_1", "kind": "direct"}},
             {"id": "pho_msg_2", "status": "received", "createdAt": "2026-11-14T08:00:01.000Z",  # a group text: ignored
              "content": {"type": "text", "text": "help"}, "sentBy": {"handle": "sim-other"}, "receivedBy": {"id": "x"},
              "space": {"id": "pho_spc_2", "kind": "group"}}]

    class M(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        def do_GET(self):
            got["auth"].add(self.headers["Authorization"])
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            assert q["direction"] == ["received"] and q["order"] == ["asc"] and q["createdSince"], q
            self._json(200, {"items": inbox if "pageToken" in q else inbox[:1], "nextPageToken": None if "pageToken" in q else "a.b"})

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            assert self.headers.get("idempotency-key") or "/typing" in self.path, self.path
            if self.path.endswith("/messages"):
                got["messages"].append(json.loads(body)); return self._json(201, {"id": "pho_msg_out", "status": "dispatched"})
            if self.path.endswith("/attachments"):
                ctype = self.headers["Content-Type"]; boundary = ctype.split("boundary=")[1]
                parts = body.split(f"--{boundary}".encode())[1:-1]
                meta = json.loads(parts[0].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n"))
                data = parts[1].split(b"\r\n\r\n", 1)[1][:-2]
                assert ctype.startswith("multipart/related") and meta["sizeBytes"] == len(data), (meta, len(data))
                got["attachments"].append(meta); return self._json(201, {"id": "pho_att_1", **meta})
            if self.path.endswith("/typing"):
                got["typing"].append(json.loads(body)["typing"]); return self._json(202, {})
            self._json(404, {"error": self.path})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), M)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    p = Photon("pho_test_key", "pho_prj_test", f"http://127.0.0.1:{srv.server_port}")
    b = Bridge(p)
    assert b.poll_once() == 3  # first page (1) + second page (2): paging followed
    assert b.since == "2026-11-14T08:00:01.000Z"
    for _ in range(50):
        if got["messages"] and got["typing"][-1:] == [False]:
            break
        time.sleep(0.1)
    m = got["messages"][0]
    assert m["spaceId"] == "pho_spc_1" and m["senderId"] == "pho_usr_line" and m["content"]["text"] == sms.HELP and m["parentMessageId"] == "pho_msg_1", m
    assert len(got["messages"]) == 1, "group text must be ignored"
    assert got["typing"] == [True, False] and got["auth"] == {"Bearer pho_test_key"}
    assert b.poll_once() == 3 and len(got["messages"]) == 1  # same items again: deduped, no second reply
    import base64, hmac  # webhook: signed event answered once, retry deduped, bad signature refused
    os.environ.update(PHOTON_WEBHOOK_SECRET="whsec_" + base64.b64encode(b"k" * 24).decode(), PHOTON_API_KEY="pho_test_key",
                      PHOTON_PROJECT_ID="pho_prj_test", PHOTON_API=f"http://127.0.0.1:{srv.server_port}")
    ev = json.dumps({"type": "message.received", "data": {"object": {**inbox[0], "id": "pho_msg_3"}}}).encode()
    ts = str(int(time.time()))
    h = {"webhook-id": "evt_1", "webhook-timestamp": ts,
         "webhook-signature": "v1," + base64.b64encode(hmac.new(b"k" * 24, f"evt_1.{ts}.".encode() + ev, hashlib.sha256).digest()).decode()}
    assert webhook({**h, "webhook-signature": "v1,bad"}, ev)[0] == 401
    assert webhook(h, ev) == (200, "answered") and webhook(h, ev) == (200, "duplicate") and len(got["messages"]) == 2
    img = os.path.join(os.path.dirname(__file__), "..", "cache", "selftest.jpg")
    open(img, "wb").write(b"\xff\xd8\xff\xe0" + os.urandom(64) + b"\xff\xd9")
    p.send_image("pho_spc_1", "pho_usr_line", img)
    os.remove(img)
    assert got["attachments"][0]["contentType"] == "image/jpeg" and got["messages"][-1]["content"] == {"type": "attachment", "attachmentId": "pho_att_1"}
    assert "sms:" + key_for("sim-crew-handle") in store._ev and "sim-crew-handle" not in json.dumps(store._ev)
    srv.shutdown()
    print("photon bridge selftest ok: poll, paging, dedupe, group skip, reply route, typing, multipart upload")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    if cmd == "selftest":
        os.environ["ANTHROPIC_API_KEY"] = ""
        selftest()
    elif cmd == "register":
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
        r = Photon(os.environ["PHOTON_API_KEY"], os.environ["PHOTON_PROJECT_ID"], os.environ.get("PHOTON_API", API)).register(sys.argv[2])
        print(f"Assigned. Text START to {r['assignedPhoneNumber']} from that phone. opted in: {r.get('optIn')}")
    else:
        sys.exit(__doc__)
