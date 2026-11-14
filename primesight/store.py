"""Shared state that must outlive one request: runs, trace steps, phone threads, notes, images.

With SUPABASE_URL + SUPABASE_SECRET_KEY set (the Vercel deploy) it lives in Supabase (supabase/schema.sql), so every
serverless instance sees the same runs. Without them it is in-process dicts, exactly like the laptop server always was.
  put/get      one JSON value per key           (kv table)
  append/read  ordered JSON items per stream    (events table)
  claim        True the first time a key is put (dedupe Photon webhook retries)
  upload       file -> public URL               (storage bucket "images")
"""
import json, os, threading, urllib.error, urllib.parse, urllib.request
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SECRET_KEY", "")
remote = bool(URL and KEY)
_kv, _ev, _lock = {}, {}, threading.Lock()


def _call(method, path, body=None, headers=None, raw=None):
    # New sb_secret_ keys go in apikey only (they are not JWTs); a legacy service_role JWT also goes in Authorization
    h = {"apikey": KEY, **({} if KEY.startswith("sb_") else {"Authorization": f"Bearer {KEY}"}), **(headers or {})}
    if body is not None:
        h["Content-Type"] = "application/json"
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(URL + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            t = r.read().decode()
            return json.loads(t) if t else None
    except urllib.error.HTTPError as e:
        if e.code == 409:
            raise KeyError("exists") from None
        raise RuntimeError(f"Supabase {method} {path.split('?')[0]} -> {e.code}: {e.read().decode()[:200]}") from None


def put(key, value):
    if not remote:
        _kv[key] = value
        return
    _call("POST", "/rest/v1/kv", {"key": key, "value": value}, {"Prefer": "resolution=merge-duplicates,return=minimal"})


def get(key, default=None):
    if not remote:
        return _kv.get(key, default)
    rows = _call("GET", "/rest/v1/kv?select=value&key=eq." + urllib.parse.quote(key))
    return rows[0]["value"] if rows else default


def claim(key):
    """True if this call put the key first (a webhook retry or a second instance gets False)."""
    if not remote:
        with _lock:
            if key in _kv:
                return False
            _kv[key] = True
            return True
    try:
        _call("POST", "/rest/v1/kv", {"key": key, "value": True}, {"Prefer": "return=minimal"})
        return True
    except KeyError:
        return False


def append(stream, item):
    extend(stream, [item])


def extend(stream, items):
    if not items:
        return
    if not remote:
        with _lock:
            _ev.setdefault(stream, []).extend(items)
        return
    _call("POST", "/rest/v1/events", [{"stream": stream, "data": i} for i in items], {"Prefer": "return=minimal"})


def read(stream, after=0):
    """Items of a stream in append order, skipping the first `after`."""
    if not remote:
        return list(_ev.get(stream, []))[after:]
    rows = _call("GET", f"/rest/v1/events?select=data&order=id.asc&offset={int(after)}&stream=eq." + urllib.parse.quote(stream))
    return [r["data"] for r in rows]


def upload(path, ctype="image/jpeg"):
    """Public URL of the uploaded file (overwrites a file of the same name)."""
    name = os.path.basename(path)
    _call("POST", f"/storage/v1/object/images/{name}", raw=open(path, "rb").read(), headers={"Content-Type": ctype, "x-upsert": "true"})
    return public(name)


def public(name):
    return f"{URL}/storage/v1/object/public/images/{name}"


if __name__ == "__main__":
    assert not remote or os.environ.get("STORE_SELFTEST_REMOTE"), "self-check runs in memory; set STORE_SELFTEST_REMOTE=1 to hit Supabase"
    k = "selftest:" + os.urandom(4).hex()
    put(k, {"a": 1}); assert get(k) == {"a": 1} and get(k + "x", 7) == 7
    put(k, {"a": 2}); assert get(k) == {"a": 2}  # upsert
    assert claim(k + ":c") and not claim(k + ":c")
    append(k, {"n": 0}); append(k, {"n": 1}); assert [i["n"] for i in read(k)] == [0, 1] and read(k, 1) == [{"n": 1}]
    print("store ok", "(supabase)" if remote else "(memory)")
