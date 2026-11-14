import json, math, os, time, requests
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
API = "https://api.vexcelgroup.com/v2"
H = {"X-API-KEY": os.environ["VEXCEL_API_KEY"]}
OUT = os.path.join(os.path.dirname(__file__), "out"); os.makedirs(OUT, exist_ok=True)
LAT, LON = 29.7584604, -95.3682126  # Wells Fargo Plaza (CoStar primaryAddress)
PT = f"POINT({LON} {LAT})"
LAYERS = "urban,urban-r,wide-area,urban-g"

def call(method, path, **kw):
    for a in range(5):
        t = time.time()
        r = requests.request(method, f"{API}/{path}", headers=H, timeout=300, **kw)
        if r.status_code != 429:
            print(f"  {method} /{path} -> {r.status_code} in {time.time()-t:.1f}s")
            return r
        time.sleep(2 ** a)
    return r

def square(half):
    dl = half / 111_320; dn = half / (111_320 * math.cos(math.radians(LAT)))
    w, e, s, n = LON - dn, LON + dn, LAT - dl, LAT + dl
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"

# 1. property attributes
r = call("GET", "property/extract", params={"wkt": PT, "srid": 4326, "layer": LAYERS, "latest": "true",
                                            "include": "precomputed", "unit-system": "imperial"})
if r.ok:
    a = r.json()["features"][0]["properties"]
    json.dump(r.json(), open(f"{OUT}/property.json", "w"), indent=1)
    print("  collection", a.get("metadata.collection"), "| structures", a.get("structure.count"),
          "| building-type", a.get("building-type.class"))
    for s in a.get("structures", [])[:3]:
        print("   ", {k: v for k, v in s.items() if any(x in k for x in ("footprint.area", "height", "roof-characteristics.material", "roof-objects", "primary"))})
else:
    print(r.text[:300])

# 2. imagery: ortho of a 150 m square, plus oblique coverage
area = square(75)
img = call("POST", "ortho/extract", json={"wkt": area, "srid": 4326, "layer": LAYERS, "crop": "bounding_box", "image-format": "jpeg",
                                          "destination-dimensions": "1200,1200"})
if img.ok: open(f"{OUT}/vx_ortho.jpg", "wb").write(img.content)
cov = call("GET", "oriented/collections", params={"wkt": area, "srid": 4326, "layer": "urban", "product-type": "oblique",
                                                  "include": "product-type,collection,last-capture-date"})
dirs = sorted({f["properties"]["product-type"] for f in cov.json().get("features", [])}) if cov.ok else []
dates = sorted({f["properties"]["last-capture-date"] for f in cov.json().get("features", [])}) if cov.ok else []
print("  oblique directions:", dirs, "| capture dates:", dates[-3:])
if "oblique-south" in dirs:
    o = call("POST", "oriented/extract", json={"wkt": square(60), "srid": 4326, "layer": "urban", "product-type": "oblique-south",
                                               "rotation": "natural", "crop": "bbox", "image-format": "jpeg", "downsample": 2})
    if o.ok: open(f"{OUT}/vx_oblique_south.jpg", "wb").write(o.content)
    else: print(o.text[:300])

# 3. open-vocabulary detection of response-relevant things around the tower
labels = ["fire hydrant", "loading dock", "parking garage entrance", "helipad", "building entrance", "fire truck"]
det = call("POST", "elements/detect", json={"wkt": area, "srid": 4326,
    "images": [{"endpoint": "ortho/extract", "request": {"layer": LAYERS, "srid": 4326, "wkt": area, "crop": "bounding_box"}}],
    "model-input": {"elements": [{"label": l, "model": "sam3", "detection-threshold": 0.4} for l in labels],
                    "include": ["bbox", "confidence"]}})
if det.ok:
    for f in det.json()["features"]:
        ds = f["properties"]["detections"]
        print(f"   {f['properties']['label']:<24} {len(ds)} found", [round(d['confidence'], 2) for d in ds][:6])
    json.dump(det.json(), open(f"{OUT}/vx_detect.json", "w"))
else:
    print(det.text[:400])

# 4. embeddings: where could an engine stage nearby?
emb = call("POST", "embedding/extract", json={"wkt": square(600), "srid": 4326, "search-text": "empty surface parking lot",
                                              "include": "score,geometry", "total-records": 5, "page-size": 5})
if emb.ok:
    for f in emb.json()["features"]:
        print("   score", round(f["properties"]["score"], 3), str(f["geometry"])[:90])
else:
    print(emb.text[:300])
