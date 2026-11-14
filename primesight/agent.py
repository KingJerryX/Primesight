"""deepagents orchestrator: a 911 call in, a crew-ready briefing out, building file updated.

python -m primesight.agent "Cardiac arrest, 30th floor, 1000 Louisiana St, Houston"

What deepagents does here:
- one virtual file system (CompositeBackend): /buildings/ crew notes and /scans/ SAM 3 results on disk,
  /playbook/ our rules, everything else per-run scratch; writes to all three are denied (permissions)
- memory: /playbook/AGENTS.md is always in the prompt; skills: one playbook per incident type, read on demand
- subagents: tenant-risk (structured flags), notify-writer (texts for building staff), run in parallel
- checkpointer: each run_id is a thread, so crew replies ("CREW: which elevator?") continue it
"""
import csv, functools, json, os, re, sys
from dotenv import load_dotenv
from deepagents import FilesystemPermission, create_deep_agent
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


def gather_building(property_id: int, floor: int, incident: str) -> str:
    """Pull all three layers (CoStar building data, Matterport scan, Vexcel aerial + hydrants) for a building and floor.
    incident is one of fire, medical, hazmat. Returns JSON with the facts, a draft briefing, image paths and known gaps."""
    f = briefing.gather(property_id, floor)
    draft = briefing.compose(f, incident)
    b = f["building"]
    return json.dumps({
        "draft_briefing": draft,
        "building": {k: b[k] for k in ("name", "address", "stories", "year_built", "construction", "sprinklers", "elevators", "contacts")},
        "tenants_on_floor": [t for t in b["tenants"] if t["floor"] == floor],
        "tenants_nearby_floors": [{"name": t["name"], "floor": t["floor"], "about": t["about"]} for t in b["tenants"]
                                  if t["floor"] and abs(t["floor"] - floor) <= 3 and t["floor"] != floor],
        "hydrants": f["hydrants"][:3], "staging": f["staging"][:2], "roof": f["roof"],
        "scan": {"id": (f["scan"] or {}).get("id"), "exact_floor": f["scan_exact"], "floor": (f["scan"] or {}).get("floor"),
                 "link": (f["scan"] or {}).get("link")},
        "vision": f["vision"],  # SAM 3 counts from the scan's panoramas (exit signs, pull stations), if precomputed
        "images": [p for p in (f["floorplan"], f["aerial"]) if p],
        "gaps": f["gaps"],
    }, default=str)


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
                     "occupancy. At most 4 flags; an empty list if none. Never invent facts the description doesn't "
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

SYSTEM = """You are Primesight. A dispatcher forwards a 911 call; you text the responding crew a building briefing before they arrive.

Steps:
1. Pull the address, floor and incident type (fire, medical or hazmat) from the call. Use find_building to get the property id.
2. Read the skill for this incident type, and call gather_building. It returns a draft briefing and the raw facts.
3. In parallel, delegate with the task tool: tenants on or near the floor to tenant-risk, and the contacts to notify-writer.
4. Read /buildings/<property_id>.md if it exists: crew notes from earlier incidents.
5. Write the final briefing (rules in your memory and the skill), then save one line about this incident with add_building_note.

Files: /buildings/ crew notes (read only; use add_building_note), /scans/<scan id>.json SAM 3 results per scan point, /playbook/ your rules.
Reply with only the briefing text."""

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
        subagents=[TENANT_RISK, NOTIFY],
        skills=["/playbook/skills/"],
        memory=["/playbook/AGENTS.md"],
        permissions=READ_ONLY,
        backend=BACKEND,
        checkpointer=SAVER,
    )


def _text(m):
    return m.content if isinstance(m.content, str) else "".join(b.get("text", "") for b in m.content if isinstance(b, dict))


def run(call_text, run_id=None):
    """One turn on thread run_id (a new call, or a CREW: reply). The trace includes the subagents' own steps."""
    from langchain_core.callbacks import get_usage_metadata_callback
    run_id = run_id or "local"
    out = None
    with get_usage_metadata_callback() as usage:  # counts every model call, subagents included
        for ns, step in build().stream({"messages": [{"role": "user", "content": call_text}]},
                                       {"configurable": {"thread_id": run_id}}, stream_mode="updates", subgraphs=True):
            who = "helper" if ns else "agent"  # ns is non-empty inside a subagent
            for upd in step.values():
                for m in (upd or {}).get("messages", []) if isinstance(upd, dict) else []:
                    for tc in getattr(m, "tool_calls", []) or []:
                        print(f"  [{who}] -> {tc['name']}({json.dumps(tc['args'])[:100]})", file=sys.stderr)
                        trace.log(run_id, tc["name"] if who == "agent" else f"helper:{tc['name']}", json.dumps(tc["args"])[:200])
                    if not ns and getattr(m, "name", None) == "task" and '"flags"' in _text(m):
                        FLAGS[run_id] = json.loads(_text(m))["flags"]
                    if not ns:
                        out = m
    tin = sum(u.get("input_tokens", 0) for u in usage.usage_metadata.values())
    tout = sum(u.get("output_tokens", 0) for u in usage.usage_metadata.values())
    cost = f"{tin} in / {tout} out tokens, ~${(tin * 4 + tout * 20) / 1e6:.2f}"  # all Opus 5.5 list price, no cache discount
    print(f"  usage: {cost}", file=sys.stderr)
    trace.log(run_id, "usage", cost)
    return _text(out)


def ask(run_id, text, call_text=None):
    """A crew reply on an earlier run's thread. If the thread is gone (server restart, replayed run), resend the call."""
    known = build().get_state({"configurable": {"thread_id": run_id}}).values.get("messages")
    return run((f"Original call: {call_text}\n" if not known and call_text else "") + f"CREW: {text}", run_id)


if __name__ == "__main__":
    assert json.loads(find_building("1000 Louisiana St, Houston"))[0]["property_id"] == 233899
    assert json.loads(find_building("Smoke on floor 30, 1000 Louisiana St, Houston"))[0]["property_id"] == 233899
    assert queue_text("property manager", "Fire on  12", {"configurable": {"thread_id": "t"}}) == "queued" \
        and OUTBOX["t"] == [{"to": "property manager", "text": "Fire on 12"}]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Add ANTHROPIC_API_KEY to .env to run the agent. (self-checks passed.)")
    print(run(" ".join(sys.argv[1:]) or "Cardiac arrest, 30th floor, 1000 Louisiana St, Houston. Caller is a coworker."))
