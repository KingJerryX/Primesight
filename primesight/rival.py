"""Beat Primesight: the dispatcher round's result screen puts the player's grade next to Primesight's own crew text for
the same building, floor and emergency, graded by the same grader (primesight/grader.py) on the same call.

POST /drill/rival {"case", "incident"} -> {"text", "score", "out_of", "parts", "checks", "sent", "source"}
                                          404 when that case has no cached run (the page then shows nothing)
python -m primesight.rival                               self-checks (no API calls)
python -m primesight.rival build [--only <case>_<incident>,...] [--force] [--draft]
    runs Primesight (server.brief: the deepagents agent with a key, the no-LLM draft without) on the dispatch text the
    First responder page would send, grades its SMS with grader.grade, and writes cache/rival/<case>_<incident>.json.
    --draft: the no-LLM briefing (what the deploy sends without a passcode). Measured Nov 15: agent ~$0.51 + grading
    ~$0.10 per run, ~55 s; draft + grading ~$0.10. Each file records its "source" (agent or draft); the page says which.

The route only reads that cache, so the result screen never waits on a live run and Vercel never runs one.
"""
import json, os, re, sys, time
from . import paths
from .console_api import FLOORS
from .drill import CALLS, INCIDENTS, WHERE, WORDS

DIR = os.path.join(paths.ROOT, "cache", "rival")  # shipped with the repo: read from ROOT, never written on Vercel
CASES = {f"{p}_{m}": (p, m) for p, m in FLOORS}
KEEP = ("case", "incident", "sent", "text", "source", "score", "out_of", "parts", "checks")


def ordinal(n):  # common.js ordinal(), so "sent" matches what the First responder page would send
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def call_text(pid, floor, iid):
    """What the game tells the grader the caller said: drill.start()'s lines after the call-taker's, joined."""
    return " ".join(line.format(where=WHERE[pid], floor=WORDS.get(floor, f"{floor}th")) for line in CALLS[iid][1:])


def post(path, body):
    if path != "/drill/rival":
        return None
    J = "application/json"
    case, iid = body.get("case"), body.get("incident")
    if not (isinstance(case, str) and case in CASES and isinstance(iid, str) and iid in INCIDENTS):
        return 400, {"error": "case must be a game floor and incident a game incident"}, J
    p = os.path.join(DIR, f"{case}_{iid}.json")
    if not os.path.exists(p):
        return 404, {"error": "no Primesight run cached for this call"}, J
    r = json.load(open(p, encoding="utf-8"))
    from .grader import timed  # timed like the player: brief_s is how long Primesight took to write it
    return 200, timed({k: r.get(k) for k in KEEP}, r.get("brief_s")), J


# ---- The builder (local only, needs the CoStar kit; with ANTHROPIC_API_KEY it runs the real agent) ----

def _usd(usage, price):  # usage: LangChain usage_metadata by model; price: (in, out) $ per million, no cache discount
    return sum(u.get("input_tokens", 0) * price[0] + u.get("output_tokens", 0) * price[1] for u in usage.values()) / 1e6


def build_one(case, iid, mode=None):
    import uuid
    from langchain_core.callbacks import get_usage_metadata_callback
    from . import grader, server, trace
    pid, mid = CASES[case]
    floor = grader.answer_key(pid, mid)["floor"]
    b = next(x for x in server.buildings() if x["pid"] == pid)
    label = INCIDENTS[iid][0]
    sent = f"{label} on the {ordinal(floor)} floor, {b['address']}, {b['city']}."  # common.js dispatchText()
    run_id, t0 = uuid.uuid4().hex[:12], time.time()
    out = server.brief(sent, run_id, mode)
    text, took = out["briefing"], time.time() - t0
    if not out["property_id"] or out["property_id"] != pid:
        raise RuntimeError(f"Primesight found {out['property_id']} for {sent!r}, not {pid}")
    m = re.search(r'"usage", "detail": "(\d+) in / (\d+) out', trace.read(run_id))
    agent_usd = (int(m.group(1)) * 4 + int(m.group(2)) * 20) / 1e6 if m else 0  # agent.run's own count, Opus 5.5 list
    with get_usage_metadata_callback() as u:
        g = grader.grade(case, iid, call_text(pid, floor, iid), text)
    if not g:
        raise RuntimeError(f"grader refused Primesight's text ({len(text)} chars)")
    if os.environ.get("ANTHROPIC_API_KEY") and g["out_of"] != 100:
        raise RuntimeError("grading team failed (code checks only): run this one again")
    source = "agent" if os.environ.get("ANTHROPIC_API_KEY") and mode != "draft" else "draft"
    row = {"case": case, "incident": iid, "sent": sent, "text": text, "source": source, **g, "brief_s": round(took, 1),
           "cost_usd": round(agent_usd + _usd(u.usage_metadata, (2, 10)), 4), "built": time.strftime("%Y-%m-%d %H:%M")}
    os.makedirs(DIR, exist_ok=True)
    json.dump(row, open(os.path.join(DIR, f"{case}_{iid}.json"), "w", encoding="utf-8"), indent=1)
    return row


def build(only=None, force=False, mode=None, workers=4):
    from concurrent.futures import ThreadPoolExecutor
    from dotenv import load_dotenv
    load_dotenv(os.path.join(paths.ROOT, ".env"))
    import tempfile
    from . import agent, briefing, grader, review, store, trace
    tips = review.lessons()  # the team's accepted ideas live in Supabase: read them before the store goes local
    review.lessons = lambda: tips
    print(f"accepted playbook ideas in each run: {tips.count(chr(10) + '- ')}", file=sys.stderr)
    store.remote = False  # this run's traces and notes stay on this laptop, never in the deploy's Supabase
    briefing.OUT = trace.DIR = tempfile.mkdtemp(prefix="rival_")  # nor in the repo's output/ images and cache/trace/
    grader.queue = lambda *a: None  # Primesight's own text must not feed the playbook review queue

    def add_building_note(property_id: int, note: str) -> str:
        return "saved"  # 30 practice runs must not write 30 lines into the crew notes the next run (and the grader) reads
    add_building_note.__doc__ = agent.add_building_note.__doc__
    agent.add_building_note = add_building_note
    if os.environ.get("ANTHROPIC_API_KEY"):
        agent.build(), grader.build()  # once, before the threads share them
    todo = [(c, i) for c in CASES for i in INCIDENTS if (not only or f"{c}_{i}" in only)
            and (force or not os.path.exists(os.path.join(DIR, f"{c}_{i}.json")))]
    print(f"building {len(todo)} runs, {workers} at a time", file=sys.stderr)
    t0, rows = time.time(), []

    def one(ci):
        try:
            r = build_one(*ci, mode)
            print(f"  {ci[0]}_{ci[1]}: {r['score']}/{r['out_of']} ${r['cost_usd']:.3f} {r['brief_s']} s", file=sys.stderr)
            return r
        except Exception as e:
            print(f"  {ci[0]}_{ci[1]}: FAILED {type(e).__name__}: {e}"[:300], file=sys.stderr)
    with ThreadPoolExecutor(workers) as ex:
        rows = [r for r in ex.map(one, todo) if r]
    print(f"{len(rows)} of {len(todo)} built in {time.time() - t0:.0f} s, ${sum(r['cost_usd'] for r in rows):.2f}", file=sys.stderr)


if __name__ == "__main__":
    if sys.argv[1:2] == ["build"]:
        args = sys.argv[2:]
        build(args[args.index("--only") + 1].split(",") if "--only" in args else None, "--force" in args,
              "draft" if "--draft" in args else None)
        sys.exit()
    assert ordinal(36) == "36th" and ordinal(1) == "1st" and ordinal(12) == "12th" and ordinal(22) == "22nd"
    assert call_text(233899, 36, "cardiac_arrest").startswith("I'm at Wells Fargo Plaza, Louisiana Street. My coworker")
    assert "thirty-sixth floor" in call_text(233899, 36, "cardiac_arrest") and "911" not in call_text(233899, 36, "fall")
    assert post("/drill/other", {}) is None
    case = next(iter(CASES))
    assert post("/drill/rival", {"case": "../../.env", "incident": "fall"})[0] == 400
    assert post("/drill/rival", {"case": case, "incident": "x" * 5000})[0] == 400
    assert post("/drill/rival", {"case": [case], "incident": "fall"})[0] == 400
    r = post("/drill/rival", {"case": case, "incident": "fall"})
    assert r[0] in (200, 404) and (r[0] == 404 or set(r[1]) == set(KEEP) | {"secs"} and "speed" in r[1]["parts"]), r
    print("rival checks pass")
