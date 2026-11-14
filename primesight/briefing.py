"""Gather all three layers for a building + floor, and write the one-screen briefing for an incident type."""
import json, os
from concurrent.futures import ThreadPoolExecutor
from . import costar, matterport, outside

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output")  # floor plans and aerials sent with briefings
INCIDENTS = ("fire", "medical", "hazmat", "rescue", "security", "utility")  # categories in agentfs/incidents.json
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

    with ThreadPoolExecutor(7) as ex:
        f_wind = ex.submit(soft, "wind", outside.wind, lat, lon)
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

    vision, frame = None, (scan or {}).pop("floorplan_frame", None)  # SAM 3 results, precomputed by vision.py
    vpath = os.path.join(ROOT, "cache", "vision", f"{(scan or {}).get('id')}.json")
    if scan and os.path.exists(vpath):
        v = json.load(open(vpath, encoding="utf-8"))
        from .vision import SIGNS, locate
        found = locate(v["sweeps"])  # sightings merged across scan points: one entry per physical sign
        vision = {"scan_points": len(v["sweeps"]), "scan_points_with": v["sweeps_with"],
                  "located": {lab: sum(g["label"] == lab for g in found) for lab in SIGNS}}
        if plan_img and frame:
            plan_img = soft("floorplan markup", mark_plan, plan_img, frame, v["sweeps"],
                            f"FLOOR {scan['floor']} - {b['name'] or b['address'].split(',')[0]}") or plan_img

    hyd, roof, stage, w, aerial = f_hyd.result() or [], f_roof.result() or {}, f_stage.result() or [], f_wind.result(), f_ortho.result()
    if aerial:  # label the plain aerial for the crew; the plain one stays on disk
        wt = (f"wind from {outside.DIRS[round(w['wind_direction_10m'] / 45) % 8]} {round(w['wind_speed_10m'])} mph, "
              f"smoke drifts {outside.DIRS[round((w['wind_direction_10m'] + 180) / 45) % 8]}") if w else ""
        aerial = soft("approach map", outside.approach_map, aerial, lat, lon, outside.HALF_M, roof.get("footprint.wkt"),
                      f"{b['name'] or b['address'].split(',')[0]}, {b['stories']} fl", hyd, stage,
                      w and w["wind_direction_10m"], wt, costar.neighbors(pid)) or aerial
    last[pid] = {"building": b, "floor": floor, "hydrants": hyd, "roof": roof, "wind": w,
            "staging": stage, "aerial": aerial, "scans": scans, "scan": scan,
            "scan_exact": exact, "floorplan": plan_img, "vision": vision, "gaps": gaps}
    return last[pid]


def plan_px(xy, frame, height):
    """Matterport model x, y (metres) -> floorplan pixel. Model y points up, image y points down."""
    (ox, oy), res = frame["origin"], frame["res"]
    return (xy[0] - ox) * res, height - (xy[1] - oy) * res


MAP_KEY = ("green EXIT = exit sign (over an exit door)", "red ALARM PULL = fire alarm pull station",
           "stairs and elevators not mapped")


def mark_plan(path, frame, sweeps, title=""):
    """Floor map for a crew's phone: each exit sign and fire alarm pull station found in the 3D scan, drawn once where it
    is and labelled on the map, a title band (e.g. 'FLOOR 36 - Wells Fargo Plaza') and a key. Sized to read at ~400 px."""
    from PIL import Image, ImageDraw, ImageFont
    from .vision import locate
    plan = Image.open(path).convert("RGB")
    W, H = plan.size
    big, small = ImageFont.load_default(size=max(22, W // 22)), ImageFont.load_default(size=max(18, W // 34))
    r, pad = max(14, W // 50), max(6, W // 200)
    signs = locate(sweeps)
    lines = MAP_KEY if signs else ("no exit signs or alarm pulls found in the 3D scan",) + MAP_KEY[2:]
    band, kh = big.size + 3 * pad, len(lines) * (small.size + pad) + 2 * pad
    im = Image.new("RGB", (W, band + H + kh), (0, 0, 0))  # title above and key below the map, never over it
    im.paste(plan, (0, band))
    d = ImageDraw.Draw(im)
    txt = lambda xy, s, f, fill="white": d.text(xy, s, font=f, fill=fill, stroke_width=max(3, W // 400), stroke_fill="black")
    at = lambda g: (plan_px(g["xy"], frame, H)[0], band + plan_px(g["xy"], frame, H)[1])
    taken = [at(g) for g in signs]
    taken = [(x - r, y - r, x + r, y + r) for x, y in taken]  # markers, then labels: nothing is written over them

    def free(box):
        return box[0] >= 0 and box[2] <= W and box[1] >= band and box[3] <= band + H and \
            not any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in taken)

    for g in signs:
        x, y = at(g)
        exit_ = g["label"] == "exit sign"
        d.ellipse((x - r, y - r, x + r, y + r), fill=(0, 200, 80) if exit_ else (230, 30, 30), outline="white", width=max(3, r // 5))
        word = "EXIT" if exit_ else "ALARM PULL"
        tw, th = d.textlength(word, font=big), big.size
        cx = min(max(0, x - tw / 2), W - tw)  # centred over/under the marker, kept inside the image
        spot = next((p for p in ((x + r + pad, y - th / 2), (x - r - pad - tw, y - th / 2), (cx, y - r - pad - th), (cx, y + r + pad))
                     if free((p[0], p[1], p[0] + tw, p[1] + th))), None)
        if spot:  # crowded: no word, the colour and the key still say what it is
            taken.append((spot[0], spot[1], spot[0] + tw, spot[1] + th))
            txt(spot, word, big, fill=(90, 255, 140) if exit_ else (255, 120, 120))
    txt((2 * pad, 1.5 * pad), (title or "FLOOR MAP")[:48], big)
    for i, s in enumerate(lines):
        txt((2 * pad, band + H + pad + i * (small.size + pad)), s, small)
    out = path.replace("_plan.jpg", "_plan_marked.jpg")
    im.save(out, quality=88)
    return out


SQFT_PER_PERSON = 100  # IBC business occupant load is 150 gross sqft/person; 100 leaves room for dense offices


def _cap(emp, spaces):
    """CoStar's employee count is sometimes company-wide (1505 staff in a 4535 sqft suite, 106 of 2572 tenants in our
    60 buildings): cap it at what the leased space can hold, when every space has a size."""
    sqft = [s.get("sqft") for s in spaces]
    return min(emp, round(sum(sqft) / SQFT_PER_PERSON)) if emp and all(sqft) else emp


def _people(tenants, floors):
    """Headcount on a set of floors, counting a multi-floor tenant once per floor share."""
    by_name = {}
    for t in tenants:
        by_name.setdefault(t["name"], []).append(t)
    total = 0
    for spaces in by_name.values():
        emp = _cap(spaces[0]["employees"] or 0, spaces)
        on = [s for s in spaces if s["floor"] in floors]
        total += round(emp * len(on) / len(spaces))
    return total


def compose(f, incident, label=None):
    """Plain-text briefing for a phone screen. Every line is a fact from a named layer; unknowns are said out loud.
    incident is a category (INCIDENTS); label is the incident type's name for the header ("Elevator entrapment")."""
    assert incident in INCIDENTS, incident
    b, fl = f["building"], f["floor"]
    on_floor = [t for t in b["tenants"] if t["floor"] == fl]
    elev = {e["type"] for e in b["elevators"]}
    L = [f"PRIMESIGHT  |  {(label or incident).upper()}  |  FLOOR {fl}",
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
    if incident in ("rescue", "utility"):
        cars = ", ".join(f"{e['count'] or '?'} {e['type'].lower()}" for e in b["elevators"])
        L.append(f"ELEVATORS: {cars or 'none on record'}  |  {b['stories']} stories")
    if incident == "security":
        near = _people(b["tenants"], set(range(fl - 2, fl + 3)) - {fl})
        if near:
            L.append(f"NEARBY: ~{near} people on floors {max(fl - 2, 1)}-{fl + 2} (excluding {fl})")
    if incident == "utility" and fl > 1:
        below = _people(b["tenants"], set(range(1, fl)))
        L.append(f"BELOW: ~{below} people on floors 1-{fl - 1} (water runs down)  |  sprinklers: {b['sprinklers']}, type {b['sprinkler_type'].lower()}")

    # Outside: the approach map (labelled aerial) carries positions; the text says what is on it
    amap = str(f.get("aerial") or "").endswith("_approach.jpg")
    on = lambda la, lo: amap and outside.on_map(b["lat"], b["lon"], la, lo)
    if incident in ("fire", "hazmat") and f["hydrants"]:
        labelled = [(m, d) for m, d, la, lo in f["hydrants"] if on(la, lo)][:2]
        L.append("HYDRANTS: " + (", ".join(f"H{i} {m} m {d}" for i, (m, d) in enumerate(labelled, 1)) + " (approach map)" if labelled
                                 else ", ".join(f"{m} m {d}" for m, d, *_ in f["hydrants"][:2])))
    w = f.get("wind")
    if incident in ("fire", "hazmat") and w:
        frm = w["wind_direction_10m"]
        L.append(f"WIND: from {outside.DIRS[round(frm / 45) % 8]} {round(w['wind_speed_10m'])} mph, smoke drifts "
                 f"{outside.DIRS[round((frm + 180) / 45) % 8]}")
    if f["staging"]:
        _, m, d, la, lo = f["staging"][0]
        L.append(f"STAGING: open lot ~{m} m {d}" + (" (S on approach map" if on(la, lo) else " (off the approach map")
                 + ", from aerial search, verify)")
    roof = f["roof"]
    if incident == "fire" and roof.get("roof-characteristics.material.class"):
        L.append(f"ROOF: {roof['roof-characteristics.material.class']}"
                 + (f", {roof['roof-objects.ac-unit.count']} AC unit(s)" if roof.get("roof-objects.ac-unit.count") else ""))

    # Inside
    # Inside: the marked floor map carries positions; never scan jargon (scan points, scan start) in crew text
    s, v = f["scan"], f.get("vision")
    if s and f.get("floorplan"):
        n = (v or {}).get("located") or {}
        marks = [f"{n[k]} {lab}{'s' if n[k] > 1 else ''} ({col})"
                 for k, lab, col in (("exit sign", "exit sign", "green"), ("fire alarm pull station", "fire alarm pull", "red")) if n.get(k)]
        L.append((f"FLOOR MAP (floor {fl}, attached)" if f["scan_exact"] else f"FLOOR MAP is floor {s['floor']}, not {fl} (same core, layout may differ)")
                 + (": " + ", ".join(marks) if marks else ""))
    if s:
        L.append(f"3D TOUR of floor {s['floor']}: {s['link']}")
    else:  # never borrow rooms or routes from somewhere else: say plainly there is nothing
        L.append("INSIDE: no interior scan" + (" with a known floor" if f["scans"] else " for this building"))
    pm = next((c for c in b["contacts"] if c["role"] == "Property manager"), None)
    if pm:
        L.append(f"PROPERTY MGR: {pm['company']} {pm['phone']}")

    unknown = [k for k, v in (("sprinkler type", b["sprinkler_type"] == "Unknown"), ("stair and elevator locations", True)) if v] + f["gaps"]
    L.append("NOT KNOWN: " + ", ".join(unknown))
    return "\n".join(L)


if __name__ == "__main__":
    t = [{"name": "A", "floor": 30, "employees": 100}, {"name": "A", "floor": 31, "employees": 100},
         {"name": "B", "floor": 30, "employees": None}]
    assert _people(t, {30}) == 50 and _people(t, {30, 31}) == 100
    assert _people([{"name": "C", "floor": 1, "employees": 1505, "sqft": 4535}], {1}) == 45  # company-wide count capped
    assert _people([{"name": "D", "floor": 1, "employees": 20, "sqft": None}], {1}) == 20  # no size: kept as is
    assert plan_px((1, 2), {"origin": (-1, 0), "res": 100}, 500) == (200, 300)  # y flips: 2 m up = 200 px from bottom
    import sys
    pid, fl, inc = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    facts = gather(pid, fl)
    print(compose(facts, inc))
    print("\nimages:", facts["floorplan"], facts["aerial"])
