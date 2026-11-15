"""The dispatcher round (demo/variants/drill.html): a 911 call comes in, the player finds the building, pulls what the
crew needs from the data screens, and writes the dispatch message that primesight/grader.py grades.

GET /drill/start[?avoid=<case>]  -> {"case", "incident": {"id", "label", "category"}, "call": [caller lines], "screens"}
GET /drill/building/<pid>        -> {"pid", "name", "address", "stories", "lat", "lon", "record": [[label, value]],
                                     "tenants": {floor: [{"name", "industry", "employees"}]}}
Grading is POST /game/grade (console_api.py) with this round's case and incident.

Stateless on purpose: the round lives in the page, so any Vercel instance can serve any step. The building search runs
in the page over GET /buildings; floor plans come from GET /floor/<pid>/<floor>.
"""
import random, re
from . import costar
from .console_api import FLOORS

# Screens per emergency category: only the data that changes this response. Tenants for fires (who is above, who needs
# help out), not for a medical crew going to one patient. No hydrant screen: finding the nearest dot on a map was dull.
SCREENS = {"medical": ["locate", "record", "plan"], "rescue": ["locate", "record", "plan"],
           "fire": ["locate", "record", "tenants", "plan"]}
INCIDENTS = {"cardiac_arrest": ("Cardiac arrest", "medical"), "fall": ("Fall", "medical"),
             "breathing": ("Breathing problem", "medical"), "elevator_entrapment": ("Elevator entrapment", "rescue"),
             "structure_fire": ("Structure fire", "fire"), "smoke": ("Smoke in building", "fire")}
# How a caller says where they are: never the clean street address, so finding the building is part of the job.
WHERE = {233953: "the Esperson building, on Travis downtown", 234022: "Northbelt, the office building on Sam Houston Parkway",
         236914: "the office building at 1225 on the North Loop, by the freeway", 233899: "Wells Fargo Plaza, Louisiana Street",
         247344: "Century Park East, in Century City"}
WORDS = {4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 36: "thirty-sixth"}
# The caller's lines, as they come in. {where} and {floor} are filled per building. The 911 call-taker's questions are left
# out: the player hears the answers. Some lines matter (breathing, CPR, people trapped), some are just panic.
CALLS = {
    "cardiac_arrest": ["911, what's the address of your emergency?", "I'm at {where}. My coworker just collapsed, oh my god.",
                       "We're on the {floor} floor.", "He's not breathing. I don't think he's breathing.",
                       "Someone's doing CPR, they know how.", "Please hurry, we don't know if there's a defibrillator up here."],
    "fall": ["911, what's the address of your emergency?", "Hi, I'm at {where}, the {floor} floor.",
             "A woman fell off a step ladder in our office. She hit her head.", "She's awake, but she's confused and there's blood.",
             "She's in her sixties, I think. She can't get up.", "Can you send someone? We're by the elevators."],
    "breathing": ["911, what's the address of your emergency?", "{where}. I'm on the {floor} floor.",
                  "My manager can't catch his breath. He's grey and sweating.", "He says his chest is tight. He's sitting down.",
                  "He's talking, but only a few words at a time.", "Should I go down and meet you?"],
    "elevator_entrapment": ["911, what's the address of your emergency?", "I'm stuck in an elevator at {where}.",
                            "We got on at the {floor} floor, it stopped between floors.", "There are four of us. One lady is panicking.",
                            "Nobody's hurt. The alarm button doesn't do anything.", "It's getting really hot in here."],
    "structure_fire": ["911, what's the address of your emergency?", "There's a fire at {where}! On the {floor} floor!",
                       "There's smoke coming from one of the offices, black smoke.", "The fire alarm's going off, people are going down the stairs.",
                       "I think some people are still up there, I don't know.", "I'm outside now, I can see smoke from the windows."],
    "smoke": ["911, what's the address of your emergency?", "I'm at {where}, {floor} floor.",
              "There's smoke in the hallway, it smells like something electrical burning.", "No flames that I can see. The alarm isn't going off.",
              "It's getting thicker by the copy room.", "Should we leave? There are a lot of us on this floor."],
}


def start(avoid=None):
    """A random building floor and emergency. avoid: the case just played, which then comes last."""
    order = sorted(random.sample(FLOORS, len(FLOORS)), key=lambda c: f"{c[0]}_{c[1]}" == avoid)
    pid, mid = order[0]
    floor = {233953: 5, 234022: 6, 236914: 7, 233899: 36, 247344: 4}[pid]  # the SAM 3-scanned floor of each FLOORS scan
    iid = random.choice(list(INCIDENTS))
    label, cat = INCIDENTS[iid]
    words = WORDS.get(floor, f"{floor}th")
    call = [line.format(where=WHERE[pid], floor=words) for line in CALLS[iid]]
    return {"case": f"{pid}_{mid}", "incident": {"id": iid, "label": label, "category": cat}, "call": call, "screens": SCREENS[cat]}


def building(pid):
    """What the record and tenant screens show for any building the player picks, right or wrong."""
    from .briefing import _cap
    try:
        b = costar.building(pid)
    except (OSError, KeyError, ValueError):
        return None
    els = ", ".join(f"{e['count'] or ''} {e['type'].lower()}".strip() for e in b["elevators"]) or "none on record"
    pm = next((c["company"] for c in b["contacts"] if c["role"] == "Property manager"), None)
    fmt = lambda n, unit: n and f"{n:,.0f} {unit}"
    record = [("Name", b["name"]), ("Address", re.sub(r"\s+,", ",", b["address"])), ("Use", b["type"]), ("Stories", b["stories"]),
              ("Built", b["year_built"]), ("Frame", b["construction"]), ("Sprinklers", b["sprinklers"]),
              ("Typical floor", fmt(b["typical_floor_sqft"], "sq ft")), ("Elevators", els), ("Property manager", pm)]
    tenants = {}
    for t in b["tenants"]:
        if t["floor"] and not t["name"].startswith("Unknown"):
            tenants.setdefault(t["floor"], []).append({"name": t["name"], "industry": None if t["industry"] == "Unknown" else t["industry"],
                                                       "employees": _cap(t["employees"], [t])})
    return {"pid": pid, "name": b["name"], "address": re.sub(r"\s+,", ",", b["address"]), "stories": b["stories"], "lat": b["lat"],
            "lon": b["lon"], "record": [[k, str(v)] for k, v in record if v not in (None, "")], "tenants": tenants}


def handle(path):
    """(status, body, content type) for a drill route, or None if the path isn't one."""
    J = "application/json"
    m = re.fullmatch(r"/drill/start(?:\?avoid=(\d+_\w{11}))?", path)
    if m:
        return 200, start(m.group(1)), J
    m = re.fullmatch(r"/drill/building/(\d{1,10})", path)
    if m:
        out = building(int(m.group(1)))
        return (200, out, J) if out else (404, {"error": "no record for that building"}, J)
    return None


if __name__ == "__main__":
    assert handle("/drill/nope") is None and handle("/drill/building/../x") is None
    for _ in range(30):
        r = start()
        assert r["case"] in {f"{p}_{m}" for p, m in FLOORS} and len(r["call"]) == 6 and "{" not in "".join(r["call"])
        assert r["screens"] == SCREENS[r["incident"]["category"]]
    assert all(start(avoid=f"{FLOORS[0][0]}_{FLOORS[0][1]}")["case"] != f"{FLOORS[0][0]}_{FLOORS[0][1]}" for _ in range(20))
    from .agent import CATALOG
    assert set(INCIDENTS) <= set(CATALOG) and all(CATALOG[i]["category"] == c for i, (_, c) in INCIDENTS.items())
    b = building(233899)
    assert b["tenants"][36][0]["name"] == "CME Group" and ["Elevators", "passenger, freight"] in b["record"], b["record"]
    assert all(not t["name"].startswith("Unknown") for ts in building(234022)["tenants"].values() for t in ts)
    assert handle("/drill/building/999999999")[0] == 404
    assert handle("/drill/outside/233899") is None
    print("drill checks pass")
