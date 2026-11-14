"""One crew conversation, whatever carries it: the Photon bridge (real iMessage/SMS) or the phone simulator page.

handle(phone, text, send) decides what the text is and calls send(msg) for each reply:
  {"type": "text", "text"}  or  {"type": "image", "url", "path", "caption"}
- a dispatch (an address we can find)  -> quick ack, then the briefing, then floorplan + aerial
- anything else in a thread with a run  -> crew follow-up, answered by the same agent thread (/ask)
- "NOTE ..." / "REPORT ..."             -> saved as a building note; the next briefing leads with it
- START / HELP                          -> how to use it
Safety: every failure still answers with something the crew can act on.
"""
import json, re, threading, time, uuid
from . import server

HELP = ("PRIMESIGHT: text a dispatch, e.g. 'Smoke on 30th floor, 1000 Louisiana St'. "
        "Reply with questions about that building, or 'NOTE <what you found>' to update it for the next crew.")
THREADS = {}  # phone -> {"run_id", "pid", "call"}
LOG = {}      # phone -> [{"n", "from", "text"|image fields, "t"}], for the simulator page
_lock = threading.Lock()


def log(phone, who, msg):
    with _lock:
        msgs = LOG.setdefault(phone, [])
        msgs.append({"n": len(msgs), "from": who, "t": round(time.time(), 2), **msg})


def looks_like_dispatch(text):
    from .agent import find_building
    return bool(re.search(r"\d+\s+\w+", text)) and json.loads(find_building(text))


def handle(phone, text, send):
    t = " ".join(text.split())
    th = THREADS.get(phone)
    try:
        if t.upper() in ("START", "HELP", "?", "HI", "HELLO"):
            return send({"type": "text", "text": HELP})
        m = re.match(r"(?i)(note|report)\s*[:\-]?\s*(.+)", t)
        if m and th:
            server.add_note(th["pid"], m.group(2))
            return send({"type": "text", "text": "PRIMESIGHT: noted for this building. The next briefing leads with it."})
        hits = looks_like_dispatch(t)
        if hits:
            send({"type": "text", "text": f"PRIMESIGHT: got it. Building the briefing for {hits[0]['address']} (about 30-60 s)."})
            run_id = uuid.uuid4().hex[:12]
            out = server.brief_or_replay(t, run_id)
            THREADS[phone] = {"run_id": run_id, "pid": out.get("property_id"), "call": t}
            send({"type": "text", "text": out["briefing"], "run_id": run_id})
            for path, url, cap in zip(out.get("images") or [], out.get("image_urls") or [], ("Floorplan", "Aerial")):
                send({"type": "image", "path": path, "url": url, "caption": f"{cap}: {out.get('property_id')}"})
            return
        if th:
            return send({"type": "text", "text": server.ask_or_replay(th["run_id"], th["call"], t)})
        send({"type": "text", "text": "PRIMESIGHT: no address found, and no briefing open on this phone. " + HELP[len("PRIMESIGHT: "):]})
    except Exception as e:  # the crew still gets an answer they can act on
        send({"type": "text", "text": f"PRIMESIGHT: briefing failed ({type(e).__name__}). Proceed on standard procedure."})


def simulate(phone, text):
    """Phone simulator: log the crew's text, answer in the background so the page can poll."""
    log(phone, "crew", {"type": "text", "text": text})
    threading.Thread(target=handle, args=(phone, text, lambda msg: log(phone, "primesight", msg)), daemon=True).start()


if __name__ == "__main__":
    import os
    os.environ["ANTHROPIC_API_KEY"] = ""  # self-check runs the free draft path (empty, so load_dotenv won't refill it)
    out = []
    handle("+15550000000", "help", out.append)
    assert out[-1]["text"] == HELP
    handle("+15550000000", "what floor is this?", out.append)
    assert "no address" in out[-1]["text"]
    handle("+15550000000", "Smoke reported on 37th floor, 1000 Louisiana St Houston", out.append)
    assert out[0 + 2]["text"].startswith("PRIMESIGHT: got it") and "FLOOR 37" in out[3]["text"], out
    handle("+15550000000", "NOTE stairwell B door sticks", out.append)
    assert out[-1]["text"].startswith("PRIMESIGHT: noted")
    print("\n".join(m.get("text") or m["url"] for m in out))
