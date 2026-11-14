import csv, json, os
from mp import gql
KIT = os.path.join(os.path.dirname(__file__), "..", "costar-hackathon-kit", "commercial-properties.csv")
ids = [r["MatterportModelId"] for r in csv.DictReader(open(KIT, encoding="utf-8")) if r["PropertyId"] == "233899"]

def tryq(label, q, mid):
    code, out = gql(q, {"id": mid})
    errs = out.get("errors") if isinstance(out, dict) else out
    return label, code, (errs[0].get("message")[:120] if errs else "ok"), out.get("data") if isinstance(out, dict) else None

for mid in ids:
    _, _, _, d = tryq("basic", "query($id:ID!){model(id:$id){name created floors{label} rooms{id} locations{id} mattertags{label}}}", mid)
    m = (d or {}).get("model") or {}
    print(mid, "|", m.get("name"), "|", (m.get("created") or "")[:10], "| floors", len(m.get("floors") or []),
          "| rooms", len(m.get("rooms") or []), "| sweeps", len(m.get("locations") or []), "| tags", [t["label"] for t in m.get("mattertags") or []][:5])

mid = ids[0]
probes = {
  "PI boundary+openings": "query($id:ID!){model(id:$id){rooms{label classifications{label} boundary{edges{type openings{type width}}}}}}",
  "PI dimensions": "query($id:ID!){model(id:$id){dimensionEstimates(units:metric){area areaIndoor} rooms{dimensionEstimates(units:metric){area width depth height}}}}",
  "geocoordinates": "query($id:ID!){model(id:$id){geocoordinates{source latitude longitude altitude}}}",
  "panos": "query($id:ID!){model(id:$id){locations{id position{x y z} panos{skybox(resolution:\"2k\"){id status format children}}}}}",
  "floorplans": "query($id:ID!){model(id:$id){assets{floorplans(provider:\"matterport\", flags:[photogramy]){floor{label} format url width height}}}}",
  "geolocation field": "query($id:ID!){model(id:$id){geolocation{lat long}}}",
  "neighbors": "query($id:ID!){model(id:$id){locations{id neighbors{id}}}}",
}
for k, q in probes.items():
    label, code, status, d = tryq(k, q, mid)
    s = json.dumps(d)
    print(f"\n[{k}] {code} {status}\n  {s[:700]}")
