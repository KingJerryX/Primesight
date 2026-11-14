"""Rank the kit's buildings by how much Primesight can say about them, and pick one scan per building.

python -m primesight.rank 60   -> cache/ranked.json, best first: the SAM 3 queue and the console's location list
Score: current tenants (with headcount), elevators, sprinkler status, property manager, height, office use.
Scan: among scans whose name gives a floor, the floor with the most current tenants (fewest scan points on a tie).
"""
import json, os, sys
from concurrent.futures import ThreadPoolExecutor
from . import costar, matterport

OUT = os.path.join(costar.CACHE, "ranked.json")


def score_all():
    """One pass over the kit: pid -> {score, floors: {floor: tenants}, address, city, type, stories}."""
    fname = {}  # propertyFloorId -> floor number
    props = {}
    for p in costar._scan("commercial-property.jsonl", lambda l: True):
        for f in p.get("floors") or []:
            fname[f["propertyFloorId"]] = costar.floor_number(costar.enum("FloorName", f.get("floorName")))
        elev = any(e.get("elevatorState") == 1 for blk in ("propertyOffice", "propertyRetail", "propertyIndustrial", "propertyFlex", "propertyApartment")
                   for e in ((p.get(blk) or {}).get("structural") or {}).get("elevators", []) + ((p.get(blk) or {}).get("elevators") or []))
        a = p.get("primaryAddress") or {}
        props[p["id"]] = {"pid": p["id"], "name": p.get("buildingName") or a.get("buildingName") or "",
                          "address": " ".join(str(a.get(k) or "") for k in ("streetNumber", "streetName", "streetType")).strip(),
                          "city": a.get("city") or "", "lat": a.get("latitude"), "lon": a.get("longitude"), "type": costar.enum("PropertyType", p.get("propertyType")),
                          "stories": p.get("numStoriesAboveGrade"), "elevators": elev, "sprinklers": p.get("hasSprinklers"),
                          "pm": any(c.get("contactRole") == 4 for c in p.get("contacts") or []), "floors": {}, "tenants": 0}
    for o in costar._scan("location-occupancy.jsonl", lambda l: '"toDate"' not in l):
        p = props.get(o["propertyId"])
        if not p:
            continue
        p["tenants"] += 1
        for fl in {fname.get(s.get("propertyFloorId")) for s in o.get("spaces") or []} - {None}:
            p["floors"][fl] = p["floors"].get(fl, 0) + 1
    for p in props.values():
        p["score"] = (min(p["tenants"], 60) + 15 * p["elevators"] + 8 * (p["sprinklers"] is not None) + 8 * p["pm"]
                      + min(p["stories"] or 0, 40) / 2 + 15 * (p["type"] == "Office"))
    return props


def pick_scan(p, mids):
    """Scan with a floor in its name, on the floor with the most tenants. None if no scan names a floor."""
    try:
        scans = [s for s in matterport.models([m for m in mids if m]) if s["floor"] is not None]
    except Exception:
        return None
    if not scans:
        return None
    best = max(scans, key=lambda s: p["floors"].get(s["floor"], 0))
    return {"scan_id": best["id"], "scan_name": best["name"], "scan_floor": best["floor"],
            "tenants_on_scan_floor": p["floors"].get(best["floor"], 0), "scans_named": len(scans)}


def rank(n):
    props, models = score_all(), costar.property_ids_with_scans()
    cands = sorted((p for pid, p in props.items() if any(models.get(pid, []))), key=lambda p: -p["score"])[:3 * n]
    with ThreadPoolExecutor(6) as ex:
        picks = list(ex.map(lambda p: pick_scan(p, models[p["pid"]]), cands))
    out = [{**{k: p[k] for k in ("pid", "name", "address", "city", "lat", "lon", "type", "stories", "tenants", "score")}, **s}
           for p, s in zip(cands, picks) if s][:n]
    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1)
    return out


if __name__ == "__main__":
    assert costar.floor_number("ThirdFloor") == 3
    for r in rank(int(sys.argv[1]) if sys.argv[1:] else 60):
        print(r["pid"], round(r["score"]), r["type"], r["stories"], r["tenants"], r["address"], r["city"], "|", r["scan_floor"], r["scan_name"])
