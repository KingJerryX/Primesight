"""CoStar kit -> one building's facts, tenants by floor and contacts. Reads the local kit; caches per property."""
import csv, json, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "costar-hackathon-kit")
CACHE = os.path.join(ROOT, "cache")  # gitignored: holds CoStar-derived data
ENUMS = json.load(open(os.path.join(KIT, "schemas", "enums.json"), encoding="utf-8"))

_NUM = {w: i for i, w in enumerate("Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen "
                                    "Fourteen Fifteen Sixteen Seventeen Eighteen Nineteen".split())}
_NUM.update({w: 10 * i for i, w in enumerate("Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split(), 2)})
_ORD = {"First": 1, "Second": 2, "Third": 3, "Fifth": 5, "Eighth": 8, "Ninth": 9, "Twelfth": 12}


def floor_number(name):
    """'SeventySixthFloor' -> 76, 'Ground' -> 1, 'Basement' -> 0. None if not a numbered floor.
    The FloorName enum's integer is NOT the floor number (29 = FifthFloor)."""
    if name in ("Ground", "Lobby", "Plaza", "FirstFloor"):
        return 1
    if name in ("Basement", "LowerLevel", "Concourse"):
        return 0
    if not name.endswith("Floor"):
        return None
    total = 0
    for w in re.findall(r"[A-Z][a-z]*", name[:-5]):
        if w in ("Hundred", "Hundredth"):
            total *= 100
        elif w in _ORD:
            total += _ORD[w]
        elif w in _NUM:
            total += _NUM[w]
        elif w.endswith("ieth") and w[:-4] + "y" in _NUM:  # Twentieth
            total += _NUM[w[:-4] + "y"]
        elif w.endswith("th") and w[:-2] in _NUM:  # Sixth, Fourteenth
            total += _NUM[w[:-2]]
        else:
            return None
    return total or None


def enum(kind, v):
    return ENUMS.get(kind, {}).get(str(v), str(v))


def _scan(fname, keep):
    with open(os.path.join(KIT, "data", fname), encoding="utf-8") as fh:
        for line in fh:
            if keep(line):
                yield json.loads(line)


def property_ids_with_scans():
    """PropertyId -> [MatterportModelId] from the kit's starter CSV."""
    out = {}
    with open(os.path.join(KIT, "commercial-properties.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.setdefault(int(r["PropertyId"]), []).append(r["MatterportModelId"])
    return out


def building(pid):
    """Return a compact, decoded building record. First call scans the kit (~10 s); later calls hit the cache."""
    path = os.path.join(CACHE, f"costar_{pid}.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))

    tag = re.compile(rf'"id":\s*{pid}[,}}]')
    prop = next((r for r in _scan("commercial-property.jsonl", lambda l: tag.search(l[:40])) if r["id"] == pid), None)
    if prop is None:
        raise KeyError(f"property {pid} not in kit")
    occ = [r for r in _scan("location-occupancy.jsonl", lambda l: f'"propertyId":{pid},' in l) if r.get("toDate") is None]

    cids = {r["tenantLocationId"] for r in occ if r.get("tenantLocationId")}
    cids |= {c["locationId"] for c in prop.get("contacts", []) if c.get("locationId")}
    companies = {}
    for r in _scan("company-detail.jsonl", lambda l: int(l[6:l.index(",")]) in cids if l.startswith('{"id":') else False):
        companies[r["id"]] = r

    floors = {f["propertyFloorId"]: floor_number(enum("FloorName", f["floorName"])) for f in prop.get("floors", [])}
    tenants = []
    for r in occ:
        c = companies.get(r.get("tenantLocationId"), {})
        for s in r.get("spaces") or [{}]:
            tenants.append({
                "name": c.get("name", "Unknown tenant"),
                "floor": floors.get(s.get("propertyFloorId")),
                "suite": (s.get("suite") or "").strip() or None,
                "sqft": s.get("squareFeetOccupied"),
                "employees": c.get("numEmployees"),
                "industry": enum("IndustryType", r.get("industryType")),
                "about": (c.get("locationBio") or "")[:300] or None,
            })

    a = prop["primaryAddress"]
    elevators = [e for blk in ("propertyOffice", "propertyRetail", "propertyIndustrial", "propertyFlex")
                 for e in ((prop.get(blk) or {}).get("structural") or {}).get("elevators", [])]
    roles = {4: "Property manager", 33: "Building superintendent", 34: "Security supervisor"}
    contacts = []
    for c in prop.get("contacts", []):
        if c.get("contactRole") in roles and c.get("locationId") in companies:
            co = companies[c["locationId"]]
            contacts.append({"role": roles[c["contactRole"]], "company": co.get("name"), "phone": co.get("locationPhone")})

    out = {
        "property_id": pid,
        "name": prop.get("buildingName") or a.get("buildingName"),
        "address": f'{a.get("streetNumber", "")} {a.get("streetName", "")} {a.get("streetType", "")}, {a.get("city")}, {a.get("state")}'.replace("  ", " "),
        "lat": a.get("latitude"), "lon": a.get("longitude"),
        "type": enum("PropertyType", prop.get("propertyType")),
        "stories": prop.get("numStoriesAboveGrade"),
        "gross_sqft": prop.get("grossBuildingAreaSquareFeet"),
        "typical_floor_sqft": prop.get("grossTypicalFloorSquareFeet"),
        "year_built": next((e["year"] for e in prop.get("constructionEvents", []) if e.get("eventType") == 2), None),
        "construction": enum("BuildingConstructionMaterialType", prop.get("buildingConstructionMaterial")),
        "sprinklers": {True: "yes", False: "no", None: "unknown"}[prop.get("hasSprinklers")],
        "sprinkler_type": enum("SprinklerType", prop.get("sprinklerType")),
        "elevators": [{"type": enum("ElevatorType", e["elevatorType"]), "count": e.get("numElevators")}
                      for e in elevators if e.get("elevatorState") == 1],
        "tenants": sorted(tenants, key=lambda t: (t["floor"] is None, t["floor"] or 0)),
        "contacts": contacts,  # phones in the kit are fake (555)
        "matterport_models": property_ids_with_scans().get(pid, []),
    }
    os.makedirs(CACHE, exist_ok=True)
    json.dump(out, open(path, "w", encoding="utf-8"), indent=1)
    return out


if __name__ == "__main__":
    import sys
    names = [v for v in ENUMS["FloorName"].values() if v.endswith("Floor") and v not in ("UnknownFloor", "PrincipalFloor")]
    nums = [floor_number(v) for v in names]
    assert None not in nums and len(set(nums)) == len(nums), [n for n, x in zip(names, nums) if x is None]
    assert floor_number("SeventySixthFloor") == 76 and floor_number("OneHundredTwentiethFloor") == 120
    b = building(int(sys.argv[1]) if len(sys.argv) > 1 else 233899)
    print(b["name"], b["stories"], "stories |", len(b["tenants"]), "tenant spaces |", len(b["contacts"]), "contacts")
