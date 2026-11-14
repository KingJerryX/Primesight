"""Outside the building: Vexcel aerial imagery + roof facts, and nearby hydrants from city open data."""
import math, os, time, requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
VX = "https://api.vexcelgroup.com/v2"
LAYERS = "urban,urban-r,wide-area,urban-g"
# City hydrant layers (ArcGIS FeatureServer). Add a city by adding a line.
HYDRANTS = {"Houston": "https://services.arcgis.com/NummVBqZSIJKUeVR/ArcGIS/rest/services/wHydrant/FeatureServer/0/query",
            # City of LA, DWP hydrants (the LA County Fire layers don't cover the City)
            "Los Angeles": "https://maps.lacity.org/lahub/rest/services/Fire_Department/MapServer/0/query"}


def _vx(method, path, **kw):
    for a in range(4):
        r = requests.request(method, f"{VX}/{path}", headers={"X-API-KEY": os.environ["VEXCEL_API_KEY"]}, timeout=120, **kw)
        if r.status_code != 429:
            r.raise_for_status()
            return r
        time.sleep(2 ** a)
    r.raise_for_status()


def square(lat, lon, half_m):
    dl = half_m / 111_320
    dn = half_m / (111_320 * math.cos(math.radians(lat)))
    w, e, s, n = lon - dn, lon + dn, lat - dl, lat + dl
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def roof(lat, lon):
    """Vexcel property attributes for the building under the point (roof material, height, footprint)."""
    p = _vx("GET", "property/extract", params={"wkt": f"POINT({lon} {lat})", "srid": 4326, "layer": LAYERS,
                                                 "latest": "true", "include": "precomputed", "unit-system": "imperial"})
    f = p.json()["features"]
    if not f:
        return {}
    a = f[0]["properties"]
    s = max(a.get("structures", []), key=lambda s: s.get("structure.footprint.area", 0) or 0, default={})
    keep = ("height", "roof-characteristics.material", "roof-objects", "footprint.area", "roof-shape")
    return {"capture_date": a.get("metadata.capture-date") or a.get("metadata.collection"),
            **{k.replace("structure.", ""): v for k, v in s.items() if any(x in k for x in keep)}}


def ortho(lat, lon, path, half_m=75):
    img = _vx("POST", "ortho/extract", json={"wkt": square(lat, lon, half_m), "srid": 4326, "layer": LAYERS,
                                              "crop": "bounding_box", "image-format": "jpeg", "destination-dimensions": "1200,1200"})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(img.content)
    return path


def oblique(lat, lon, path, side="south", half_m=60):
    o = _vx("POST", "oriented/extract", json={"wkt": square(lat, lon, half_m), "srid": 4326, "layer": "urban",
                                               "product-type": f"oblique-{side}", "rotation": "natural", "crop": "bbox",
                                               "image-format": "jpeg", "downsample": 2})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(o.content)
    return path


def staging(lat, lon, text="empty surface parking lot", radius_m=400, n=3):
    """Open areas near the building by Vexcel image-embedding search. Returns [(score, lat, lon)]."""
    r = _vx("POST", "embedding/extract", json={"wkt": square(lat, lon, radius_m), "srid": 4326, "search-text": text,
                                                "include": "score,geometry", "total-records": n, "page-size": n})
    from shapely.geometry import shape
    cs = [(f["properties"]["score"], shape(f["geometry"]).centroid) for f in r.json()["features"]]
    return [(round(s, 3), *bearing(lat, lon, c.y, c.x), c.y, c.x) for s, c in cs]  # (score, m, dir, lat, lon)


def bearing(lat1, lon1, lat2, lon2):
    """Distance in metres and compass direction from point 1 to point 2."""
    dy = (lat2 - lat1) * 111_320
    dx = (lon2 - lon1) * 111_320 * math.cos(math.radians(lat1))
    deg = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    return round(math.hypot(dx, dy)), "N NE E SE S SW W NW".split()[round(deg / 45) % 8]


def hydrants(lat, lon, city, radius_m=150):
    """Hydrants within radius, nearest first: [(metres, direction, lat, lon)]. Empty if the city has no layer."""
    url = HYDRANTS.get(city.strip().title())  # CoStar has "houston" too
    if not url:
        return []
    r = requests.get(url, params={"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
                                  "distance": radius_m, "units": "esriSRUnit_Meter", "outFields": "OBJECTID",
                                  "returnGeometry": "true", "outSR": 4326, "f": "json"}, timeout=30)
    r.raise_for_status()
    pts = [(f["geometry"]["y"], f["geometry"]["x"]) for f in r.json().get("features", [])]
    return sorted((*bearing(lat, lon, y, x), y, x) for y, x in pts)


if __name__ == "__main__":
    assert bearing(0, 0, 0.001, 0)[1] == "N" and bearing(0, 0, 0, -0.001)[1] == "W"
    lat, lon = 29.7584604, -95.3682126
    print("hydrants", hydrants(lat, lon, "Houston")[:3])
    print("roof", roof(lat, lon))
    print("staging", staging(lat, lon))
