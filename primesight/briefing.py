"""Gather all three layers for a building + floor, and write the one-screen briefing for an incident type."""
import json, os
from concurrent.futures import ThreadPoolExecutor
from . import costar, matterport, outside

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output")  # gitignored: sponsor imagery
INCIDENTS = ("fire", "medical", "hazmat")
last = {}  # pid -> latest gather() result, so the server can find the images the agent pulled


def gather(pid, floor):
    """Everything the briefing needs. Network calls run in parallel; each layer fails soft into `gaps`."""
    b = costar.building(pid)
    lat, lon, gaps = b["lat"], b["lon"], []

    def soft(name, fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            gaps.append(f"{name} unavailable ({type(e).__name__})")
            return None

    with ThreadPoolExecutor(6) as ex:
        f_hyd = ex.submit(soft, "hydrants", outside.hydrants, lat, lon, b["address"].split(", ")[-2])
        f_roof = ex.submit(soft, "roof data", outside.roof, lat, lon)
        f_stage = ex.submit(soft, "staging search", outside.staging, lat, lon)
        f_ortho = ex.submit(soft, "aerial image", outside.ortho, lat, lon, os.path.join(OUT, f"{pid}_aerial.jpg"))
        f_scans = ex.submit(soft, "3D scans", matterport.models, b["matterport_models"])
        scans = f_scans.result() or []

    scan, exact, plan_img = None, False, None
    if scans:
        pick, exact = matterport.pick(scans, floor)
        if pick:
            scan = soft("scan detail", matterport.detail, pick["id"])
            if scan and scan["floorplan_url"]:
                plan_img = soft("floorplan", matterport.download, scan["floorplan_url"], os.path.join(OUT, f"{pid}_{pick['id']}_plan.jpg"))
            if scan:
                scan["floor"] = pick["floor"]
                scan.pop("floorplan_url")
                for s in scan["sweeps"]:
                    s.pop("faces")  # signed URLs; vision pass fetches its own
    else:
        gaps.append("no 3D scan for this building")

    vision = None  # SAM 3 results, precomputed on the GPU by vision.py (cache/vision/<scan id>.json)
    vpath = os.path.join(ROOT, "cache", "vision", f"{(scan or {}).get('id')}.json")
    if scan and os.path.exists(vpath):
        v = json.load(open(vpath, encoding="utf-8"))
        vision = {"scan_points": len(v["sweeps"]), "scan_points_with": v["sweeps_with"]}

    last[pid] = {"building": b, "floor": floor, "hydrants": f_hyd.result() or [], "roof": f_roof.result() or {},
            "staging": f_stage.result() or [], "aerial": f_ortho.result(), "scans": scans, "scan": scan,
            "scan_exact": exact, "floorplan": plan_img, "vision": vision, "gaps": gaps}
    return last[pid]


def _people(tenants, floors):
    """Headcount on a set of floors, counting a multi-floor tenant once per floor share."""
    by_name = {}
    for t in tenants:
        by_name.setdefault(t["name"], []).append(t)
    total = 0
    for spaces in by_name.values():
        emp = spaces[0]["employees"] or 0
        on = [s for s in spaces if s["floor"] in floors]
        total += round(emp * len(on) / len(spaces))
    return total


def compose(f, incident):
    """Plain-text briefing for a phone screen. Every line is a fact from a named layer; unknowns are said out loud."""
    assert incident in INCIDENTS, incident
    b, fl = f["building"], f["floor"]
    on_floor = [t for t in b["tenants"] if t["floor"] == fl]
    elev = {e["type"] for e in b["elevators"]}
    L = [f"PRIMESIGHT  |  {incident.upper()}  |  FLOOR {fl}",
         f"{b['name'] or ''} - {b['address']}".strip(" -"),
         f"{b['stories']} stories  |  built {b['year_built'] or '?'}  |  {b['construction'].lower()} frame  |  sprinklers: {b['sprinklers']}"]

    # Who's there
    if on_floor:
        names = sorted({(t["name"], t["suite"]) for t in on_floor}, key=lambda x: x[1] or "")
        L.append("ON FLOOR: " + "; ".join(f"{n}" + (f" (ste {s})" if s else "") for n, s in names[:4])
                 + (f" +{len(names) - 4} more" if len(names) > 4 else ""))
        ppl = _people(b["tenants"], {fl})
        if ppl:
            L.append(f"~{ppl} people on this floor by tenant headcount (daytime)")
    else:
        L.append("ON FLOOR: no tenant on record (vacant, or not in data)")

    if incident == "fire" and b["stories"]:
        above = _people(b["tenants"], set(range(fl + 1, b["stories"] + 1)))
        if above:
            L.append(f"ABOVE FIRE: ~{above} people on floors {fl + 1}-{b['stories']}")
    if incident == "medical":
        L.append("ELEVATOR: freight car on record (stretcher)" if "Freight" in elev
                 else "ELEVATOR: passenger only on record; freight unknown" if elev else "ELEVATOR: none on record")
    if incident == "hazmat":
        inds = sorted({t["industry"] for t in b["tenants"] if t["industry"] not in ("Unknown", "0")})
        L.append("TENANT INDUSTRIES: " + (", ".join(inds[:5]) or "not recorded"))

    # Outside
    if incident in ("fire", "hazmat") and f["hydrants"]:
        L.append("HYDRANTS: " + ", ".join(f"{m} m {d}" for m, d, *_ in f["hydrants"][:2]))
    if f["staging"]:
        _, m, d, *_ = f["staging"][0]
        L.append(f"OPEN LOT for staging ~{m} m {d} (aerial search, verify)")
    roof = f["roof"]
    if incident == "fire" and roof.get("roof-characteristics.material.class"):
        L.append(f"ROOF: {roof['roof-characteristics.material.class']}"
                 + (f", {roof['roof-objects.ac-unit.count']} AC unit(s)" if roof.get("roof-objects.ac-unit.count") else ""))

    # Inside
    s = f["scan"]
    if s:
        L.append(("INSIDE: 3D scan of this floor" if f["scan_exact"] else f"INSIDE: no scan of {fl}; nearest is floor {s['floor']} (same core, layout may differ)")
                 + f"  |  {len(s['sweeps'])} scan points  |  {s['link']}")
    v = f.get("vision")
    if v:
        w = v["scan_points_with"]
        L.append(f"EXIT SIGNS seen at {w['exit sign']} of {v['scan_points']} scan points"
                 + (f", pull stations at {w['fire alarm pull station']}" if w["fire alarm pull station"] else "")
                 + ("" if f["scan_exact"] else f" (floor {s['floor']} scan)"))
    pm = next((c for c in b["contacts"] if c["role"] == "Property manager"), None)
    if pm:
        L.append(f"PROPERTY MGR: {pm['company']} {pm['phone']}")

    unknown = [k for k, v in (("sprinkler type", b["sprinkler_type"] == "Unknown"), ("stair locations", True)) if v] + f["gaps"]
    L.append("NOT KNOWN: " + ", ".join(unknown))
    return "\n".join(L)


if __name__ == "__main__":
    t = [{"name": "A", "floor": 30, "employees": 100}, {"name": "A", "floor": 31, "employees": 100},
         {"name": "B", "floor": 30, "employees": None}]
    assert _people(t, {30}) == 50 and _people(t, {30, 31}) == 100
    import sys
    pid, fl, inc = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    facts = gather(pid, fl)
    print(compose(facts, inc))
    print("\nimages:", facts["floorplan"], facts["aerial"])
