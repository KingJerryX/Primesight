"""Routes for the dispatch console (Lane B): a building's 3D scans by floor, and Vexcel aerial views on demand.

GET /scans/<pid>               -> [{"id", "name", "floor"}], every Matterport scan of the building (floor is null if the name doesn't say)
GET /aerial/<pid>/<view>.jpg   -> Vexcel image of the building; view is top, north, east, south or west
GET /floor/<pid>/<floor>       -> {"floor", "exact", "image", "aspect", "points": [{"kind", "x", "y"}]}: the scanned floor nearest to
                                  <floor>, its plan image, and where SAM 3 located exit signs and alarm pulls (x, y as fractions of the image)
GET /floorimg/<scan id>.jpg    -> that floor plan image

The game (demo/variants/game.html): report the nearest hydrant, the exit signs and the elevators of a real building, on a clock.
POST /game/start  {"avoid"?}                -> {"round", "building", "floor", "plan", "hydrants", "span_m", "record", "ask"}  (no answers); one of FLOORS
POST /game/go     {"round"}                 -> {"ok"}; starts that round's clock, once (the briefing screen before it is not timed)
POST /game/answer {"round", "q", "answer"}  -> {"ok", "fact"}; a wrong report costs PENALTY seconds; the last right one adds "done"
POST /game/name   {"round", "name"}         -> the leaderboard, with this top-10 run on it
GET  /game/board                            -> [{"name", "seconds", "date"}], fastest first, 10 at most
POST /game/grade  {"case", "incident", "call", "message"} -> the player's dispatch message graded (primesight/grader.py):
                  {"score", "out_of", "parts", "checks", "claims", "delivery", "feedback", "suggestions_queued"}
"""
import json, math, os, random, re, tempfile, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
from . import costar, matterport, outside

TMP = os.path.join(tempfile.gettempdir(), "primesight-console")  # outside the repo, so sponsor imagery never lands in git
_ids = _ranked = None


def scans(pid):
    global _ids
    path = os.path.join(TMP, f"scans_{pid}.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    if _ids is None:
        _ids = costar.property_ids_with_scans()

    def one(mid):  # a scan that fails to load is left out rather than failing the building
        try:
            return matterport.models([mid])
        except Exception:
            return []
    with ThreadPoolExecutor(8) as ex:
        out = [s for found in ex.map(one, _ids.get(pid, [])) for s in found]
    os.makedirs(TMP, exist_ok=True)
    json.dump(out, open(path, "w", encoding="utf-8"))
    return out


def aerial(pid, view):
    """Path of the cached image, fetching it from Vexcel the first time. None if we have no location or Vexcel has no image."""
    global _ranked
    path = os.path.join(TMP, f"{pid}_{view}.jpg")
    if os.path.exists(path):
        return path
    if _ranked is None:
        _ranked = {r["pid"]: r for r in json.load(open(os.path.join(costar.CACHE, "ranked.json"), encoding="utf-8"))}
    b = _ranked.get(pid)
    if not b or not b.get("lat"):
        return None
    try:
        return outside.ortho(b["lat"], b["lon"], path) if view == "top" else outside.oblique(b["lat"], b["lon"], path, side=view)
    except Exception:
        return None


def floor_view(pid, floor):
    """The scanned floor nearest to `floor`: its plan image and the signs SAM 3 located on it. None if no scan names a floor.
    The first call per scan asks Matterport for the plan's frame and saves a 1600 px copy of the image; later calls read the cache."""
    pick, exact = matterport.pick(scans(pid), floor)
    if not pick:
        return None
    mid = pick["id"]
    meta_path = os.path.join(TMP, f"floor_{mid}.json")
    if os.path.exists(meta_path):
        meta = json.load(open(meta_path, encoding="utf-8"))
    else:
        from PIL import Image
        d = matterport.detail(mid)
        if not d["floorplan_url"]:
            return None
        src = os.path.join(costar.ROOT, "output", f"{pid}_{mid}_plan.jpg")  # already on disk for buildings that have been briefed
        if not os.path.exists(src):
            src = matterport.download(d["floorplan_url"], os.path.join(TMP, f"{mid}_full.jpg"))
        im = Image.open(src)
        meta = {"frame": d["floorplan_frame"], "size": im.size}
        im.thumbnail((1600, 1600))
        im.convert("RGB").save(os.path.join(TMP, f"{mid}_plan.jpg"), quality=85)
        json.dump(meta, open(meta_path, "w", encoding="utf-8"))
    (w, h), points = meta["size"], []
    vpath = os.path.join(costar.CACHE, "vision", f"{mid}.json")
    if os.path.exists(vpath):
        from .briefing import plan_px
        from .vision import locate
        for g in locate(json.load(open(vpath, encoding="utf-8"))["sweeps"]):  # one entry per physical sign
            x, y = plan_px(g["xy"], meta["frame"], h)
            if 0 <= x <= w and 0 <= y <= h:
                points.append({"kind": g["label"], "x": round(x / w, 4), "y": round(y / h, 4)})
    return {"floor": pick["floor"], "exact": exact, "image": f"/floorimg/{mid}.jpg", "aspect": round(w / h, 4), "points": points}


# ---- The game. No LLM: every answer is checked against the same data the briefing uses. ----
PENALTY = 15  # seconds added per wrong report, so guessing loses to looking
TIE = 1.15  # a hydrant within 15 % (or 5 m) of the nearest counts as the nearest: the eye can't rank them on a tilted map
HYDRANT_M, HYDRANT_N = 250, 12  # hydrants within this many metres are looked up; the nearest N go on the map
GO_LIMIT = 30  # a briefing left open longer than this many seconds starts the clock anyway
BOARD = os.path.join(costar.CACHE, "game_leaderboard.txt")  # JSON. cache/*.txt is in .gitignore: player names stay out of the public repo
ROUNDS, _lock = {}, threading.Lock()  # round id -> answers and clock; in memory, so a restart drops rounds in play (not the board)
JSON = "application/json"
# The five buildings the game is played on, as (CoStar property id, Matterport scan id of the floor). Change the game's
# buildings here and nowhere else. Each has all three answers in the data: city hydrants around it, 1-8 exit signs
# located by SAM 3 on a marked floor map in output/, and elevators in the CoStar record.
FLOORS = [(233953, "wovF4kpoaw4"),  # Esperson, 808 Travis St, Houston, floor 5
          (234022, "Fv5Q2P8Kmsf"),  # Northbelt, 363 Sam Houston Pky, Houston, floor 6
          (236914, "PfDGxZeDnkz"),  # 1225 Loop Fwy, Houston, floor 7
          (233899, "jUnF84zghQk"),  # Wells Fargo Plaza, 1000 Louisiana St, Houston, floor 36 (CoStar has no passenger count: freight only)
          (247344, "gacELDAHQEZ")]  # Century Park East, Los Angeles, floor 4 (hydrants from the City of LA layer)


def nearest(metres):
    """Which of these distances count as "the nearest hydrant": the lowest, and any within 5 m or TIE of it. Returns indexes."""
    low = min(metres)
    return [i for i, m in enumerate(metres) if m <= max(low + 5, low * TIE)]


def _hydrants(b):
    """The city's hydrants around the building, cached. [] if the city has no layer; a layer that is down is not cached."""
    path = os.path.join(TMP, f"hydrants_{b['property_id']}_{HYDRANT_M}.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    try:
        hyd = outside.hydrants(b["lat"], b["lon"], b["address"].split(", ")[-2], radius_m=HYDRANT_M)
    except Exception:
        return []
    os.makedirs(TMP, exist_ok=True)
    json.dump(hyd, open(path, "w", encoding="utf-8"))
    return hyd


def case(pid, mid):
    """One scanned floor as a round: (what the player sees, {question: answer}, {question: the fact in words}, the hydrants).
    A question is asked only if the data can check it: hydrants where the city layer has any, exits where SAM 3 located
    1-8 signs (more can't be counted on the map), elevators where CoStar lists any. None unless all three hold, so a floor
    whose hydrant layer is down right now is skipped rather than half asked.
    The hydrant answer is a click on a map marker: the page gets the markers in random order and answers with an index."""
    from .vision import locate
    b, v = costar.building(pid), json.load(open(os.path.join(costar.CACHE, "vision", f"{mid}.json"), encoding="utf-8"))
    ask, answers, report = {}, {}, {}
    hyd = _hydrants(b)[:HYDRANT_N] if b["lat"] else []
    hyd = random.sample(hyd, len(hyd))  # no order: a marker's index says nothing about its distance
    if hyd and min(hyd)[0] >= 10:
        ask["hydrant"], answers["hydrant"] = True, nearest([h[0] for h in hyd])
        report["hydrant"] = None  # written when the player reports one: the hydrant they clicked, in words
    exits = sum(g["label"] == "exit sign" for g in locate(v["sweeps"]))
    if 1 <= exits <= 8 and v.get("floor") is not None:
        ask["exits"], answers["exits"] = True, exits
        report["exits"] = f"Floor {v['floor']}: {exits} exit sign{'s' * (exits > 1)} in the 3D scan."
    cars = {e["type"]: e["count"] for e in b["elevators"]}
    if "Passenger" in cars or "Freight" in cars:
        count = cars.get("Passenger")  # CoStar sometimes lists a type with no count: then only freight yes/no is asked
        ask["elevators"] = {"passenger": count is not None}
        answers["elevators"] = {"freight": "Freight" in cars, **({"passenger": count} if count is not None else {})}
        report["elevators"] = "Elevators on record: " + ", ".join(f"{c or ''} {t.lower()}".strip() for t, c in cars.items()) + "."
    if len(ask) < 3:
        return None
    fmt = lambda n, unit: n and f"{n:,.0f} {unit}"
    addr = re.sub(r"\s+,", ",", b["address"])  # the kit has "1801 Century Park , Los Angeles"
    pm = next((c["company"] for c in b["contacts"] if c["role"] == "Property manager"), None)
    record = [("Name", b["name"]), ("Address", addr), ("Use", b["type"]), ("Stories", b["stories"]), ("Built", b["year_built"]),
              ("Frame", b["construction"]), ("Sprinklers", b["sprinklers"]), ("Gross area", fmt(b["gross_sqft"], "sq ft")),
              ("Typical floor", fmt(b["typical_floor_sqft"], "sq ft")),
              ("Elevators", ", ".join(f"{c or ''} {t.lower()}".strip() for t, c in cars.items()) or "none on record"),
              ("Tenants on record", len(b["tenants"])), ("Property manager", pm)]
    far = sorted(h[0] for h in hyd)[:4][-1]  # the map opens wide enough to show the four nearest hydrants
    view = {"building": {"name": b["name"], "address": addr, "stories": b["stories"], "lat": b["lat"], "lon": b["lon"]},
            "floor": v.get("floor"), "ask": ask, "plan": f"/img/{pid}_{mid}_plan_marked.jpg",
            "hydrants": [[la, lo] for _, _, la, lo in hyd], "span_m": max(80, far),
            "record": [[k, str(x)] for k, x in record if x not in (None, "")]}
    return view, answers, report, hyd


def start(avoid=None):
    """A new round on one of FLOORS, at random. Its clock starts with go(), after the briefing screen."""
    order = sorted(random.sample(FLOORS, len(FLOORS)), key=lambda c: f"{c[0]}_{c[1]}" == avoid)  # the floor just played goes last
    for pid, mid in order:
        try:
            c = case(pid, mid)
        except (OSError, KeyError, ValueError):  # no SAM 3 result or CoStar record on this machine: not playable
            c = None
        if c:
            rid = uuid.uuid4().hex[:12]
            with _lock:
                if len(ROUNDS) >= 500:  # abandoned rounds: drop the oldest
                    ROUNDS.pop(next(iter(ROUNDS)))
                ROUNDS[rid] = {"answers": c[1], "report": c[2], "hyd": c[3], "left": set(c[1]), "wrong": 0, "total": None, "saved": False,
                               "made": time.monotonic(), "t0": None}
            return {"round": rid, "case": f"{pid}_{mid}", "penalty": PENALTY, **c[0]}
    return None


def go(rid):
    """Start a round's clock, once: reading the briefing is not timed. Stalling on the briefing gains GO_LIMIT seconds at most."""
    with _lock:
        r = ROUNDS.get(rid)
        if not r:
            return None
        if r["t0"] is None:
            r["t0"] = min(time.monotonic(), r["made"] + GO_LIMIT)
        return {"ok": True}


def answer(rid, q, a):
    """Check one report. A right one comes back with the fact in words; right answers are never sent before they are given."""
    with _lock:
        r = ROUNDS.get(rid)
        if not r or r["t0"] is None or r["total"] is not None or q not in r["left"]:
            return None
        want = r["answers"][q]
        # hydrant: the index of any marker that counts as nearest. Others: exact, compared as JSON so true is not 1
        ok = (type(a) is int and a in want) if q == "hydrant" else json.dumps(a, sort_keys=True) == json.dumps(want, sort_keys=True)
        if not ok:
            r["wrong"] += 1
            return {"ok": False, "penalty": PENALTY}
        if q == "hydrant":
            r["report"][q] = f"Hydrant: {r['hyd'][a][0]} m {r['hyd'][a][1]} of the building."
        r["left"].discard(q)
        out = {"ok": True, "fact": r["report"][q]}
        if not r["left"]:
            clock = time.monotonic() - r["t0"]
            r["total"] = round(clock + PENALTY * r["wrong"], 1)
            out["done"] = {"clock": round(clock, 1), "wrong": r["wrong"], "seconds": r["total"], "report": list(r["report"].values()),
                           "rank": rank(r["total"])}
        return out


def board():
    try:
        return json.load(open(BOARD, encoding="utf-8"))
    except (OSError, ValueError):
        return []


def rank(seconds):
    """Place (1-10) this time would take on the board, or None. A tie goes below the earlier run."""
    place = sum(e["seconds"] <= seconds for e in board()) + 1
    return place if place <= 10 else None


def clean_name(name):
    """Display names are untrusted: capitals, digits, space, dash and underscore only, 1-8 characters. None if it doesn't fit."""
    name = " ".join(str(name).upper().split())
    return name if re.fullmatch(r"[A-Z0-9 _-]{1,8}", name) else None


def save_name(rid, name):
    """Put a finished top-10 round on the board, once. Returns the new board, or None if the round or name doesn't qualify."""
    name = clean_name(name)
    with _lock:
        r = ROUNDS.get(rid)
        if not name or not r or r["total"] is None or r["saved"] or not rank(r["total"]):
            return None
        top = sorted(board() + [{"name": name, "seconds": r["total"], "date": time.strftime("%Y-%m-%d")}], key=lambda e: e["seconds"])[:10]
        tmp = BOARD.replace(".txt", ".new.txt")  # also ignored by git; replaced in one step so a crash can't leave half a board
        json.dump(top, open(tmp, "w", encoding="utf-8"), indent=1)
        os.replace(tmp, BOARD)
        r["saved"] = True
        return top


def _grade(body):
    from .grader import grade
    return grade(body.get("case"), body.get("incident"), body.get("call", ""), body.get("message"), body.get("gid"))


def post(path, body):
    """(status, body, content type) for a game POST, or None if the path isn't one."""
    if not path.startswith("/game/"):
        return None
    body = body if isinstance(body, dict) else {}
    out = {"/game/start": lambda: start(body.get("avoid")), "/game/go": lambda: go(body.get("round")),
           "/game/answer": lambda: answer(body.get("round"), body.get("q"), body.get("answer")),
           "/game/name": lambda: save_name(body.get("round"), body.get("name")),
           "/game/grade": lambda: _grade(body)}.get(path, lambda: None)()
    return (200, out, JSON) if out is not None else (400, {"error": "no such round or route, or the round or name doesn't qualify"}, JSON)


def handle(path):
    """(status, body, content type) for a console route, or None if the path isn't one."""
    if path == "/game/board":
        return 200, board(), JSON
    m = re.fullmatch(r"/scans/(\d{1,10})", path)
    if m:
        return 200, scans(int(m.group(1))), "application/json"
    m = re.fullmatch(r"/aerial/(\d{1,10})/(top|north|east|south|west)\.jpg", path)
    if m:
        p = aerial(int(m.group(1)), m.group(2))
        return (200, open(p, "rb").read(), "image/jpeg") if p else (404, {"error": "no aerial image for this view"}, "application/json")
    m = re.fullmatch(r"/floor/(\d{1,10})/(\d{1,3})", path)
    if m:
        try:
            fv = floor_view(int(m.group(1)), int(m.group(2)))
        except Exception:  # Matterport or the network is down: the page shows the map instead
            fv = None
        return (200, fv, JSON) if fv else (404, {"error": "no floor plan for this building"}, JSON)
    m = re.fullmatch(r"/floorimg/(\w{11})\.jpg", path)
    if m:
        p = os.path.join(TMP, f"{m.group(1)}_plan.jpg")
        return (200, open(p, "rb").read(), "image/jpeg") if os.path.exists(p) else (404, {"error": "no floor plan image"}, JSON)
    return None


if __name__ == "__main__":
    assert handle("/buildings") is None and handle("/aerial/1/../top.jpg") is None and handle("/aerial/1/up.jpg") is None
    assert len(FLOORS) == 5 and len({pid for pid, _ in FLOORS}) == 5
    for pid, mid in FLOORS:  # every game floor has its marked map, SAM 3 result and CoStar record on disk
        for f in (f"output/{pid}_{mid}_plan_marked.jpg", f"cache/vision/{mid}.json", f"cache/costar_{pid}.json"):
            assert os.path.exists(os.path.join(costar.ROOT, f)), f
    # the game: answer checking, penalties, name rules and the board, on a made-up round and a throwaway board file
    assert nearest([60, 46, 46, 61]) == [1, 2] and nearest([137, 154, 165]) == [0, 1] and nearest([20, 26, 24.9]) == [0, 2]
    assert clean_name(" ace  1 ") == "ACE 1" and clean_name("<b>x</b>") is None and clean_name("") is None and clean_name("NINECHARS") is None
    assert post("/buildings", {}) is None and post("/game/answer", [1])[0] == 400 and post("/game/nope", {})[0] == 400
    BOARD = os.path.join(tempfile.mkdtemp(), "board.txt")
    ROUNDS["t"] = {"answers": {"hydrant": [0, 2], "exits": 1, "elevators": {"freight": True, "passenger": 2}}, "wrong": 0, "total": None,
                   "report": {"hydrant": None, "exits": "one exit", "elevators": "two cars"}, "hyd": [(46, "E", 0, 0), (61, "N", 0, 0), (46, "S", 0, 0)],
                   "left": {"hydrant", "exits", "elevators"}, "saved": False, "made": time.monotonic() - 100, "t0": None}
    assert answer("t", "exits", 1) is None and go("nope") is None  # no reports before the clock starts
    assert go("t") == {"ok": True} and 69 < time.monotonic() - ROUNDS["t"]["t0"] < 71  # briefing left open: the clock started at GO_LIMIT
    t0 = ROUNDS["t"]["t0"] = time.monotonic()
    assert go("t") == {"ok": True} and ROUNDS["t"]["t0"] == t0  # a second go doesn't restart the clock
    assert save_name("t", "ACE") is None  # not finished yet
    assert answer("t", "hydrant", 1) == {"ok": False, "penalty": PENALTY} and answer("t", "hydrant", True)["ok"] is False
    assert answer("t", "hydrant", 2) == {"ok": True, "fact": "Hydrant: 46 m S of the building."} and answer("t", "hydrant", 2) is None
    assert answer("t", "exits", True)["ok"] is False and answer("t", "exits", 1) == {"ok": True, "fact": "one exit"}  # true is not 1
    assert answer("t", "elevators", {"passenger": 2, "freight": False})["ok"] is False
    done = answer("t", "elevators", {"passenger": 2, "freight": True})["done"]
    assert done["wrong"] == 4 and 60 <= done["seconds"] < 61 and done["rank"] == 1 and answer("t", "exits", 1) is None
    assert done["report"] == ["Hydrant: 46 m S of the building.", "one exit", "two cars"]
    assert save_name("t", "<script>") is None and save_name("t", "ace")[0]["name"] == "ACE" and save_name("t", "ACE") is None  # once only
    json.dump([{"name": "X", "seconds": float(n), "date": ""} for n in range(10)], open(BOARD, "w"))
    assert rank(9.0) is None and rank(8.9) == 10 and rank(0.0) == 2 and len(board()) == 10
    ROUNDS["u"] = {**ROUNDS["t"], "total": 4.5, "saved": False}
    assert [e["seconds"] for e in save_name("u", "B-2")] == [0, 1, 2, 3, 4, 4.5, 5, 6, 7, 8] and board()[5]["name"] == "B-2"
    print("game checks pass")
    print(len(scans(233899)), "scans for 233899;", aerial(233899, "top"))
