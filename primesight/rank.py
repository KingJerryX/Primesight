"""Rank buildings for the demo: most scanned floors, most tenants on those floors, most building facts.

python -m primesight.rank            # writes cache/rank.json, prints the top 15
Pass 1 (local, one read of the kit) scores every building that has a scan.
Pass 2 asks Matterport for scan names of the top TOP buildings only, to learn which floors are scanned.
"""
import json, os, re, sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from . import costar, matterport, outside

TOP = 60
ROLES = {4, 33, 34}  # property manager, building superintendent, security supervisor


def pass1():
    scans = costar.property_ids_with_scans()
    props, floor_of = {}, {}
    rid = re.compile(r'"id":\s*(\d+)')
    for line in open(os.path.join(costar.KIT, "data", "commercial-property.jsonl"), encoding="utf-8"):
        m = rid.search(line[:40])
        if not m or int(m.group(1)) not in scans:
            continue
        p = json.loads(line)
        a = p.get("primaryAddress") or {}
        for f in p.get("floors", []):
            floor_of[f["propertyFloorId"]] = costar.floor_number(costar.enum("FloorName", f["floorName"]))
        props[p["id"]] = {
            "property_id": p["id"], "name": p.get("buildingName") or a.get("buildingName"),
            "city": a.get("city"), "state": a.get("state"), "stories": p.get("numStoriesAboveGrade") or 0,
            "sprinklers_known": p.get("hasSprinklers") is not None,
            "elevators": any(((p.get(b) or {}).get("structural") or {}).get("elevators")
                             for b in ("propertyOffice", "propertyRetail", "propertyIndustrial", "propertyFlex")),
            "contacts": sum(c.get("contactRole") in ROLES for c in p.get("contacts", [])),
            "scans": len(scans[p["id"]]),
        }
    tenants, tfloors = defaultdict(int), defaultdict(set)
    rpid = re.compile(r'"propertyId":(\d+),')
    for line in open(os.path.join(costar.KIT, "data", "location-occupancy.jsonl"), encoding="utf-8"):
        m = rpid.search(line)
        if not m or int(m.group(1)) not in props:
            continue
        r = json.loads(line)
        if r.get("toDate") is not None:
            continue
        tenants[r["propertyId"]] += 1
        for s in r.get("spaces") or []:
            if floor_of.get(s.get("propertyFloorId")) is not None:
                tfloors[r["propertyId"]].add(floor_of[s["propertyFloorId"]])
    for pid, b in props.items():
        b["tenants"], b["tenant_floors"] = tenants[pid], sorted(tfloors[pid])
        b["score1"] = (2 * min(b["scans"], 15) + len(b["tenant_floors"]) + min(b["tenants"], 60) / 3
                       + min(b["stories"], 60) / 4 + 4 * min(b["contacts"], 3) + 2 * b["sprinklers_known"] + 2 * b["elevators"])
    return sorted(props.values(), key=lambda b: -b["score1"])


def pass2(b):
    ids = costar.property_ids_with_scans()[b["property_id"]]
    try:
        s = matterport.models(ids)
    except Exception as e:
        b["scan_error"] = type(e).__name__
        s = []
    b["scan_floors"] = sorted({x["floor"] for x in s if x["floor"] is not None})
    b["scan_names"] = [x["name"] for x in s][:20]
    b["scanned_tenant_floors"] = sorted(set(b["scan_floors"]) & set(b["tenant_floors"]))
    # what the demo needs: scanned floors that also have tenants (an exact-floor briefing), plus everything else
    b["score"] = (6 * len(b["scanned_tenant_floors"]) + 3 * len(b["scan_floors"]) + b["score1"]
                  + (5 if b["city"] in outside.HYDRANTS else 0))  # hydrant layer already wired up
    return b


if __name__ == "__main__":
    ranked = pass1()
    print(f"pass 1: {len(ranked)} buildings scored", file=sys.stderr)
    with ThreadPoolExecutor(8) as ex:
        top = sorted(ex.map(pass2, ranked[:TOP]), key=lambda b: -b["score"])
    json.dump({"top": top, "pass1": ranked[:300]}, open(os.path.join(costar.CACHE, "rank.json"), "w", encoding="utf-8"), indent=1)
    print(f"{'pid':>8} {'score':>5} {'st':>3} {'ten':>4} {'scan':>4}  scanned floors w/ tenants | name, city")
    for b in top[:15]:
        print(f"{b['property_id']:>8} {b['score']:>5.0f} {b['stories']:>3} {b['tenants']:>4} {b['scans']:>4}  "
              f"{b['scanned_tenant_floors'][:10]} | {b['name']}, {b['city']}")
