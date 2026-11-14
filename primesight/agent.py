"""deepagents orchestrator: a 911 call in, a crew-ready briefing out, building file updated.

python -m primesight.agent "Cardiac arrest, 30th floor, 1000 Louisiana St, Houston"

What deepagents does here:
- one virtual file system (CompositeBackend): /buildings/ crew notes and /scans/ SAM 3 results on disk,
  /playbook/ our rules, everything else per-run scratch; writes to all three are denied (permissions)
- memory: /playbook/AGENTS.md is always in the prompt; skills: one playbook per incident type, read on demand
- subagents: tenant-risk (structured flags), notify-writer (texts for building staff), inside-scout (walks over the
  scan-point graph to SAM 3 finds), outside-scout (live wind from Open-Meteo, hydrants within 300 m, roof), run in parallel
- /playbook/incidents.json: 26 incident types in 6 categories; gather_building returns what the type needs first
- checkpointer: each run_id is a thread, so crew replies ("CREW: which elevator?") continue it
- RubricMiddleware: a grader checks each answer against a rubric; on a fail the agent revises (max 2 rounds)
"""
import csv, functools, json, os, re, sys
from dotenv import load_dotenv
from deepagents import FilesystemPermission, RubricMiddleware, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel
from . import briefing, costar, trace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))
MODEL = "claude-opus-5-5"
PLAYBOOK = os.path.join(ROOT, "primesight", "agentfs")  # our own rules: AGENTS.md memory + incident skills
OUTBOX = {}  # run_id -> texts notify-writer queued for building contacts (shown on the projector, never auto-sent)
FLAGS = {}  # run_id -> tenant-risk flags (structured), for the projector


def find_building(address: str) -> str:
    """Find the building for a street address. Returns JSON list of {property_id, address, city} matches (best first)."""
    want = re.sub(r"[^a-z0-9 ]", "", address.lower()).split()
    nums = {w for w in want if w.isdigit()}  # a call may also say "floor 30": any number can be the street number
    hits = {}
    with open(os.path.join(costar.KIT, "commercial-properties.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            words = re.sub(r"[^a-z0-9 ]", "", f'{r["Address"]} {r["City"]}'.lower()).split()
            if nums and words[:1] and words[0] not in nums:
                continue
            score = sum(w in words for w in want)
            if score >= 2:
                hits[int(r["PropertyId"])] = (score, r["Address"], r["City"])
    best = sorted(hits.items(), key=lambda kv: -kv[1][0])[:3]
    return json.dumps([{"property_id": pid, "address": a, "city": c} for pid, (_, a, c) in best])


CATALOG = {t["id"]: t for t in json.load(open(os.path.join(PLAYBOOK, "incidents.json"), encoding="utf-8"))["types"]}


def gather_building(property_id: int, floor: int, incident: str, incident_type: str = "") -> str:
    """Pull all three layers (CoStar building data, Matterport scan, Vexcel aerial + hydrants) for a building and floor.
    incident is the category: fire, medical, hazmat, rescue, security or utility. incident_type is the id from
    /playbook/incidents.json (e.g. elevator_entrapment), if one fits. Returns JSON with the facts, a draft briefing,
    what this incident type needs first, image paths and known gaps."""
    f = briefing.gather(property_id, floor)
    ty = CATALOG.get(incident_type) or {}
    draft = briefing.compose(f, incident if incident in briefing.INCIDENTS else ty.get("category", "medical"), ty.get("label"))
    b = f["building"]
    return json.dumps({
        "draft_briefing": draft,
        "incident_needs": ty.get("needs"),  # what the crew needs first for this type, from the catalog
        "building": {k: b[k] for k in ("name", "address", "stories", "year_built", "construction", "sprinklers", "elevators", "contacts")},
        "tenants_on_floor": [t for t in b["tenants"] if t["floor"] == floor],
        "tenants_nearby_floors": [{"name": t["name"], "floor": t["floor"], "about": t["about"]} for t in b["tenants"]
                                  if t["floor"] and abs(t["floor"] - floor) <= 3 and t["floor"] != floor],
        "hydrants": f["hydrants"][:3], "staging": f["staging"][:2], "roof": f["roof"],
        "scan": {"id": (f["scan"] or {}).get("id"), "exact_floor": f["scan_exact"], "floor": (f["scan"] or {}).get("floor"),
                 "link": (f["scan"] or {}).get("link")},
        # SAM 3 counts from the scan's panoramas (exit signs, pull stations), if precomputed; the walk route is the
        # WALK line in draft_briefing (its point lists would only cost tokens)
        "vision": {k: x for k, x in (f["vision"] or {}).items() if k != "route"} or None,
        "images": [p for p in (f["floorplan"], f["aerial"]) if p],
        "gaps": f["gaps"],
    }, default=str)


@functools.lru_cache(maxsize=32)
def _scan_graph(scan_id):
    from . import matterport
    return matterport.detail(scan_id)["sweeps"]  # one Matterport query per scan, then cached for the server's life


def walk_to(scan_id: str, target: str) -> str:
    """Walk over a scanned floor's scan points (Matterport) to the nearest point within ~4 m of a SAM 3 find.
    target: 'exit sign', 'fire alarm pull station', 'fire extinguisher' or 'stairs'. Starts at the scan start
    (where the scanner began, not necessarily the entry). Returns JSON {metres, legs, points_with_target} or why not."""
    from . import matterport, vision
    p = os.path.join(ROOT, "cache", "vision", f"{scan_id}.json")
    if target not in vision.LABELS:
        return json.dumps({"error": f"target must be one of {vision.LABELS}"})
    if not os.path.exists(p):
        return json.dumps({"error": "no SAM 3 results for this scan"})
    v = json.load(open(p, encoding="utf-8"))
    near = [s["id"] for s in v["sweeps"] if any(h["label"] == target and vision.kept(h) and vision.metres(h) <= 4 for h in s["hits"])]
    seen = sum(any(h["label"] == target and vision.kept(h) for h in s["hits"]) for s in v["sweeps"])
    sweeps = _scan_graph(scan_id)
    r = matterport.route(sweeps, sweeps[0]["id"], near)
    if not r or not r["exit"]:
        return json.dumps({"target": target, "seen_from_points": seen, "near_points": 0, "result": "none seen up close"})
    return json.dumps({"target": target, "seen_from_points": seen, "near_points": len(near), "metres": round(r["exit"]["m"]),
                       "legs": [[round(m), t] for m, t in r["exit"]["legs"]], "far_end_metres": round(r["far"]["m"])})


COMPASS = "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split()


def compass(deg):
    return COMPASS[round(deg / 22.5) % 16]


@functools.lru_cache(maxsize=64)
def _wind(lat, lon, slot):  # slot: 10-minute bucket, so a cached reading is never older than that
    import requests
    r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
        "latitude": lat, "longitude": lon, "current": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m,precipitation",
        "wind_speed_unit": "mph", "temperature_unit": "fahrenheit"})
    r.raise_for_status()
    return r.json()["current"]


@functools.lru_cache(maxsize=64)
def _hydrants_wide(lat, lon, city):
    from . import outside
    return outside.hydrants(lat, lon, city, radius_m=300)


def outside_now(property_id: int) -> str:
    """The live outside picture for one building: current wind from Open-Meteo (where smoke or gas drifts), every hydrant
    within 300 m with distance and direction (city hydrant layer), and roof data (Vexcel). Returns JSON; a layer that
    fails says so instead of failing the call."""
    import time
    b = costar.building(int(property_id))
    lat, lon = round(b["lat"], 4), round(b["lon"], 4)
    out = {"building": b["address"]}
    try:
        w = _wind(lat, lon, int(time.time() // 600))
        frm = compass(w["wind_direction_10m"])
        out["wind"] = {"from": frm, "drifts_toward": compass(w["wind_direction_10m"] + 180), "mph": w["wind_speed_10m"],
                       "gust_mph": w["wind_gusts_10m"], "temp_f": w["temperature_2m"], "precip_mm": w["precipitation"],
                       "observed_utc": w["time"], "source": "Open-Meteo model, 10 m above ground"}
    except Exception as e:
        out["wind"] = {"error": f"wind unavailable ({type(e).__name__})"}
    try:
        h = _hydrants_wide(lat, lon, b["address"].split(", ")[-2])
        out["hydrants"] = {"within_300m": len(h), "nearest": [{"m": round(m), "dir": d} for m, d, *_ in h[:6]]}
    except Exception as e:
        out["hydrants"] = {"error": f"hydrant layer unavailable ({type(e).__name__})"}
    try:
        from . import outside
        out["roof"] = outside.roof(b["lat"], b["lon"])
    except Exception as e:
        out["roof"] = {"error": f"roof data unavailable ({type(e).__name__})"}
    return json.dumps(out, default=str)


def add_building_note(property_id: int, note: str) -> str:
    """Append one dated line to the building's notes file (/buildings/<property_id>.md). Never overwrites earlier notes."""
    from datetime import date
    d = os.path.join(ROOT, "cache", "buildings")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{int(property_id)}.md"), "a", encoding="utf-8") as fh:
        fh.write(f"- {date.today().isoformat()} {' '.join(note.split())}\n")
    return "saved"


def queue_text(to: str, text: str, config: RunnableConfig) -> str:
    """Queue one short SMS for a building contact. to: 'building engineer' or 'property manager'.
    Queued texts are shown to the dispatcher; nothing is sent from here."""
    run_id = config.get("configurable", {}).get("thread_id")
    OUTBOX.setdefault(run_id, []).append({"to": to, "text": " ".join(text.split())})  # run() traces it as helper:queue_text
    return "queued"


class Flag(BaseModel):
    tenant: str
    floor: int | None
    risk: str  # e.g. "sedated patients", "children", "chemicals"
    why: str
    from_name_only: bool


class TenantFlags(BaseModel):
    flags: list[Flag]


TENANT_RISK = {
    "name": "tenant-risk",
    "description": "Reads tenant names and descriptions for one building and flags occupants that change the response: "
                   "vulnerable people (daycare, clinic, surgery, school, elderly care), hazardous materials (labs, battery "
                   "or chemical storage), or high occupancy. Give it the incident, floor and the tenant list as JSON.",
    "system_prompt": "You assess building tenants for first responders. From each tenant's name, industry and description, "
                     "flag only what changes how a crew responds: vulnerable occupants, likely hazardous materials, unusual "
                     "occupancy. Never flag a tenant as a likely target or threat because of its religion, ethnicity, nationality or politics. At most 4 flags; an empty list if none. Never invent facts the description doesn't "
                     "support; set from_name_only when inferring from a name.",
    "model": ChatAnthropic(model=MODEL, max_tokens=2000),
    "tools": [],  # isolated: no building tools, just the tenants it is given
    "response_format": TenantFlags,  # JSON back to the parent, and to the projector via FLAGS
}

NOTIFY = {
    "name": "notify-writer",
    "description": "Writes and queues short texts for the building's own people (building engineer, property manager) "
                   "so they can meet the crew. Give it the incident type, floor, address, which elevator/entrance the "
                   "data supports, and the contacts list from gather_building.",
    "system_prompt": "You write texts from a fire department to a building's staff during an incident. Queue at most one "
                     "text per contact role with queue_text, each under 200 characters, plain ASCII. Say what is happening "
                     "and where (floor), and ask for one concrete thing the data supports: meet the crew at the main "
                     "entrance, recall the freight elevator to the lobby, bring keys and fire panel access. Never "
                     "invent arrival times, names or entrances. Reply 'queued: <roles>' when done.",
    "model": ChatAnthropic(model=MODEL, max_tokens=1500),
    "tools": [queue_text],
}

INSIDE_SCOUT = {
    "name": "inside-scout",
    "description": "Answers where-is questions about the inside of one scanned floor from SAM 3 results: walk distance from "
                   "the scan start to the nearest exit sign, pull station, extinguisher or stairs. Give it the scan id and "
                   "what to find. Use it only when gather_building says the scan is of this exact floor and vision is not null.",
    "system_prompt": "You answer questions about one scanned floor for a fire crew. Use walk_to once per thing asked "
                     "(at most 3 calls). Report each as one short line: '<thing>: ~N m from scan start (legs)' or 'not seen up "
                     "close'. Distances are walks over 3D-scan points from where the scanner began; never call that point the "
                     "entry or the lobby. Stairs detections are unreliable: say so if you report them. Nothing else.",
    "model": ChatAnthropic(model=MODEL, max_tokens=1200),
    "tools": [walk_to],
}

OUTSIDE_SCOUT = {
    "name": "outside-scout",
    "description": "Reports the live outside picture for a building: wind now (where smoke or gas will drift), every hydrant "
                   "within 300 m, roof data. Give it the property id and the incident. Use it for fire, hazmat and gas or "
                   "smoke calls, where wind and water supply change the approach.",
    "system_prompt": "You report outside conditions for a fire crew. Call outside_now once. Reply in at most 2 lines: "
                     "'WIND: from <dir> <mph> mph (gusts <n>), smoke/gas drifts <dir> | <temp>F' and 'WATER: <n> hydrants "
                     "within 300 m, nearest <m> m <dir>, <m> m <dir>'. If a layer has an error, write '<layer> unavailable'. "
                     "Report facts only: never say where to stage or which side to approach from.",
    "model": ChatAnthropic(model=MODEL, max_tokens=800),
    "tools": [outside_now],
}

SYSTEM = """You are Primesight. A dispatcher forwards a 911 call; you text the responding crew a building briefing before they arrive.

Steps:
1. Pull the address, floor and incident from the call. The category is one of fire, medical, hazmat, rescue, security,
   utility; the type is an id from /playbook/incidents.json (e.g. elevator_entrapment, gas_leak, active_threat) if one fits.
   Use find_building to get the property id.
2. Read the skill for the category, and call gather_building with the category and type. It returns a draft briefing,
   what this incident type needs first (incident_needs), and the raw facts.
3. In parallel, delegate with the task tool: tenants on or near the floor to tenant-risk, the contacts to notify-writer,
   and, only if the scan is of this exact floor and vision is not null, the scan id to inside-scout (fire and security:
   pull station and exit sign; medical and rescue: exit sign; hazmat and utility: fire extinguisher). For fire, hazmat,
   gas or smoke calls, and any type whose incident_needs mention wind or temperature (heat illness, unknown odor), also
   send the property id to outside-scout: live wind, temperature and every hydrant within 300 m.
4. Read /buildings/<property_id>.md if it exists: crew notes from earlier incidents.
5. Write the final briefing (rules in your memory and the skill), then save one line about this incident with add_building_note.

Files: /buildings/ crew notes (read only; use add_building_note), /playbook/ your rules, /scans/<scan id>.json SAM 3 results
per scan point. gather_building already summarizes the scan results in "vision"; if vision is null there is no /scans file, so don't look.
Reply with only the briefing text."""

# Graded before anything goes out: a grader model checks the final text against these, and the agent revises on a fail.
RUBRIC_BRIEF = """The final message is the crew briefing. Criteria:
1. At most 12 lines, plain ASCII, no markdown or bullets.
2. Every fact appears in a tool result in the transcript (gather_building, crew notes, tenant-risk). Nothing invented: no stair, standpipe or AED locations, names, entrances or arrival times that the tools did not give.
3. It has a NOT KNOWN line, and if the 3D scan is from a different floor it says so.
4. It states facts, not orders: no tactical instructions such as where to search or which stair to use.
5. Tenant risks inferred from a name only are marked possible or "from name only".
6. The first line after the header is the most urgent fact for this incident type (fire: people above; medical: elevator or patient location; hazmat: tenant risk; rescue: elevators or keys; security: people on and near the floor; utility: sprinklers or elevators)."""

RUBRIC_REPLY = """The final message answers a crew reply (the latest message starting "CREW:"). Criteria:
1. At most 3 lines, plain ASCII.
2. Facts come only from tool results or crew notes in the transcript; anything else is said to be not known.
3. If the crew reported something about the building, add_building_note was called for it."""

GRADER = RubricMiddleware(model=ChatAnthropic(model=MODEL, max_tokens=2000), max_iterations=2)

# One file view for the agent: persistent folders routed to disk, everything else is per-run scratch in graph state.
BACKEND = CompositeBackend(default=StateBackend(), routes={
    "/buildings/": FilesystemBackend(root_dir=os.path.join(ROOT, "cache", "buildings"), virtual_mode=True),
    "/scans/": FilesystemBackend(root_dir=os.path.join(ROOT, "cache", "vision"), virtual_mode=True),
    "/playbook/": FilesystemBackend(root_dir=PLAYBOOK, virtual_mode=True),
})
# Notes only grow through add_building_note (dated, append-only); scans and rules are never edited by the agent.
READ_ONLY = [FilesystemPermission(operations=["write"], paths=["/buildings/**", "/scans/**", "/playbook/**"], mode="deny")]
SAVER = InMemorySaver()  # ponytail: in-memory threads die with the server; SqliteSaver if replies must survive restarts


@functools.cache
def build():
    return create_deep_agent(
        model=ChatAnthropic(model=MODEL, max_tokens=8000),
        tools=[find_building, gather_building, add_building_note],
        system_prompt=SYSTEM,
        subagents=[TENANT_RISK, NOTIFY, INSIDE_SCOUT, OUTSIDE_SCOUT],
        skills=["/playbook/skills/"],
        memory=["/playbook/AGENTS.md"],
        permissions=READ_ONLY,
        backend=BACKEND,
        checkpointer=SAVER,
        middleware=[GRADER],
    )


def _text(m):
    return m.content if isinstance(m.content, str) else "".join(b.get("text", "") for b in m.content if isinstance(b, dict))


def run(call_text, run_id=None, rubric=RUBRIC_BRIEF):
    """One turn on thread run_id (a new call, or a CREW: reply). The trace includes the subagents' own steps."""
    from langchain_core.callbacks import get_usage_metadata_callback
    run_id = run_id or "local"
    cfg = {"configurable": {"thread_id": run_id}}
    seen = len(build().get_state(cfg).values.get("_rubric_evaluations") or [])
    out = None
    with get_usage_metadata_callback() as usage:  # counts every model call, subagents included
        for ns, step in build().stream({"messages": [{"role": "user", "content": call_text}], "rubric": rubric},
                                       cfg, stream_mode="updates", subgraphs=True):
            who = "helper" if ns else "agent"  # ns is non-empty inside a subagent
            for upd in step.values():
                for m in (upd or {}).get("messages", []) if isinstance(upd, dict) else []:
                    for tc in getattr(m, "tool_calls", []) or []:
                        print(f"  [{who}] -> {tc['name']}({json.dumps(tc['args'])[:100]})", file=sys.stderr)
                        trace.log(run_id, tc["name"] if who == "agent" else f"helper:{tc['name']}", json.dumps(tc["args"])[:200])
                    if not ns and getattr(m, "name", None) == "task" and '"flags"' in _text(m):
                        FLAGS[run_id] = json.loads(_text(m))["flags"]
                    if not ns and m.type == "ai":  # grader feedback comes back as a human message: skip it
                        out = m
    for ev in (build().get_state(cfg).values.get("_rubric_evaluations") or [])[seen:]:  # this turn's grades
        gaps = "; ".join(f"{c['name']}: {c.get('gap', '')}" for c in ev["criteria"] if not c["passed"])
        trace.log(run_id, "graded", f"{ev['result']}" + (f" - {gaps}" if gaps else ""))
    tin = sum(u.get("input_tokens", 0) for u in usage.usage_metadata.values())
    tout = sum(u.get("output_tokens", 0) for u in usage.usage_metadata.values())
    cost = f"{tin} in / {tout} out tokens, ~${(tin * 4 + tout * 20) / 1e6:.2f}"  # all Opus 5.5 list price, no cache discount
    print(f"  usage: {cost}", file=sys.stderr)
    trace.log(run_id, "usage", cost)
    return _text(out)


def ask(run_id, text, call_text=None):
    """A crew reply on an earlier run's thread. If the thread is gone (server restart, replayed run), resend the call."""
    known = build().get_state({"configurable": {"thread_id": run_id}}).values.get("messages")
    return run((f"Original call: {call_text}\n" if not known and call_text else "") + f"CREW: {text}", run_id, RUBRIC_REPLY)


if __name__ == "__main__":
    assert json.loads(find_building("1000 Louisiana St, Houston"))[0]["property_id"] == 233899
    assert json.loads(find_building("Smoke on floor 30, 1000 Louisiana St, Houston"))[0]["property_id"] == 233899
    assert queue_text("property manager", "Fire on  12", {"configurable": {"thread_id": "t"}}) == "queued" \
        and OUTBOX["t"] == [{"to": "property manager", "text": "Fire on 12"}]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Add ANTHROPIC_API_KEY to .env to run the agent. (self-checks passed.)")
    print(run(" ".join(sys.argv[1:]) or "Cardiac arrest, 30th floor, 1000 Louisiana St, Houston. Caller is a coworker."))
