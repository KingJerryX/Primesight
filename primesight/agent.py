"""deepagents orchestrator: a 911 call in, a crew-ready briefing out, building file updated.

python -m primesight.agent "Cardiac arrest, 30th floor, 1000 Louisiana St, Houston"
"""
import csv, json, os, re, sys
from dotenv import load_dotenv
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_anthropic import ChatAnthropic
from . import briefing, costar, trace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))
MODEL = "claude-opus-5-5"


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
        "scan": {"exact_floor": f["scan_exact"], "floor": (f["scan"] or {}).get("floor"), "link": (f["scan"] or {}).get("link")},
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


TENANT_RISK = {
    "name": "tenant-risk",
    "description": "Reads tenant names and descriptions for one building and flags occupants that change the response: "
                   "vulnerable people (daycare, clinic, school, elderly care), hazardous materials (labs, battery or chemical "
                   "storage), or high occupancy. Give it the tenant list as JSON.",
    "system_prompt": "You assess building tenants for first responders. From each tenant's name, industry and description, "
                     "flag only what changes how a crew responds: vulnerable occupants, likely hazardous materials, unusual "
                     "occupancy. Reply in at most 3 short lines, each: tenant (floor) - risk - why. Say 'no flags' if none. "
                     "Never invent facts that the description doesn't support; say 'possible' when inferring from a name.",
    "model": ChatAnthropic(model=MODEL, max_tokens=2000),
}

SYSTEM = f"""You are Primesight. A dispatcher forwards a 911 call; you text the responding crew a building briefing before they arrive.

Steps:
1. Pull the address, floor and incident type (fire, medical or hazmat) from the call. Use find_building to get the property id.
2. Call gather_building. It returns a draft briefing built from the data plus the raw facts.
3. If there are tenants on or near the floor, send them to the tenant-risk subagent.
4. Read /buildings/<property_id>.md if it exists: notes from earlier incidents (wrong door, locked stairs) outrank the data.
5. Write the final briefing, then save one line about this incident with add_building_note (it adds the date; never use write_file on /buildings).

The final briefing is SMS text, plain ASCII, at most 12 lines, most urgent first, read by a crew in a moving truck:
- Lead with what this incident type needs (medical: elevator, suite, who to ask for; fire: people above, hydrants, standpipe/stairs unknowns; hazmat: tenant materials, approach upwind).
- Keep every fact the draft states; only use facts the tools returned. Never guess stair, AED or standpipe locations.
- Keep the NOT KNOWN line. If the scan is from a different floor, say so.
- End with the scan link if there is one.
Reply with only the briefing text."""


def build():
    return create_deep_agent(
        model=ChatAnthropic(model=MODEL, max_tokens=8000),
        tools=[find_building, gather_building, add_building_note],
        system_prompt=SYSTEM,
        subagents=[TENANT_RISK],
        backend=FilesystemBackend(root_dir=os.path.join(ROOT, "cache"), virtual_mode=True),  # /buildings/*.md live in cache/
    )


def run(call_text, run_id=None):
    from langchain_core.callbacks import get_usage_metadata_callback
    agent = build()
    out = None
    with get_usage_metadata_callback() as usage:  # counts every model call, subagents included
        for step in agent.stream({"messages": [{"role": "user", "content": call_text}]}, stream_mode="updates"):
            for node, upd in step.items():  # trace for the second screen
                for m in (upd or {}).get("messages", []) if isinstance(upd, dict) else []:
                    for tc in getattr(m, "tool_calls", []) or []:
                        print(f"  [{node}] -> {tc['name']}({json.dumps(tc['args'])[:100]})", file=sys.stderr)
                        trace.log(run_id, tc["name"], json.dumps(tc["args"])[:200])
                    out = m
    tin = sum(u.get("input_tokens", 0) for u in usage.usage_metadata.values())
    tout = sum(u.get("output_tokens", 0) for u in usage.usage_metadata.values())
    cost = f"{tin} in / {tout} out tokens, ~${(tin * 4 + tout * 20) / 1e6:.2f}"  # Opus 5.5 list price, no cache discount
    print(f"  usage: {cost}", file=sys.stderr)
    trace.log(run_id, "usage", cost)
    return out.content if isinstance(out.content, str) else "".join(b.get("text", "") for b in out.content if isinstance(b, dict))


if __name__ == "__main__":
    assert json.loads(find_building("1000 Louisiana St, Houston"))[0]["property_id"] == 233899
    assert json.loads(find_building("Smoke on floor 30, 1000 Louisiana St, Houston"))[0]["property_id"] == 233899
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Add ANTHROPIC_API_KEY to .env to run the agent. (find_building self-check passed.)")
    print(run(" ".join(sys.argv[1:]) or "Cardiac arrest, 30th floor, 1000 Louisiana St, Houston. Caller is a coworker."))
