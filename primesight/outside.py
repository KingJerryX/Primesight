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
    keep = ("height", "roof-characteristics.material", "roof-objects", "footprint.area", "footprint.wkt", "roof-shape")
    return {"capture_date": a.get("metadata.capture-date") or a.get("metadata.collection"),
            **{k.replace("structure.", ""): v for k, v in s.items() if any(x in k for x in keep)}}


def wind(lat, lon):
    """Current wind and weather at a point (Open-Meteo model, no key). wind_direction_10m is where wind blows FROM."""
    r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
        "latitude": lat, "longitude": lon, "current": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,temperature_2m,precipitation",
        "wind_speed_unit": "mph", "temperature_unit": "fahrenheit"})
    r.raise_for_status()
    return r.json()["current"]


HALF_M = 120  # aerial covers 240 m x 240 m: the building plus the closest hydrants and lots


def ortho(lat, lon, path, half_m=HALF_M):
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


DIRS = "N NE E SE S SW W NW".split()


def on_map(lat, lon, la, lo, half_m=HALF_M):
    """Is (la, lo) inside the aerial square centred on (lat, lon)? Map and briefing text number hydrants with this."""
    return abs(la - lat) * 111_320 < half_m and abs(lo - lon) * 111_320 * math.cos(math.radians(lat)) < half_m


def approach_map(src, lat, lon, half_m=HALF_M, footprint=None, title="", hydrants=(), staging=(), wind_from=None,
                 wind_text="", neighbors=()):
    """Label the aerial for a crew: target building outlined, hydrants H1.., staging lot S (an arrow at the edge if it is
    off the image), smoke-drift arrow from the wind, neighbouring buildings, north arrow and a 50 m scale.
    src is the plain ortho from ortho(); writes <src>_approach.jpg and returns its path.
    hydrants: [(m, dir, lat, lon)], staging: [(score, m, dir, lat, lon)], neighbors: [{name, stories, sprinklers, lat, lon}]."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(src).convert("RGB")
    W, H = im.size
    dl, dn = half_m / 111_320, half_m / (111_320 * math.cos(math.radians(lat)))
    px = lambda la, lo: ((lo - (lon - dn)) / (2 * dn) * W, ((lat + dl) - la) / (2 * dl) * H)
    inside = lambda p: 0 <= p[0] < W and 0 <= p[1] < H
    over = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    big, small = ImageFont.load_default(size=max(22, W // 30)), ImageFont.load_default(size=max(18, W // 40))
    txt = lambda xy, s, f=small, fill="white": d.text(xy, s, font=f, fill=fill, stroke_width=4, stroke_fill="black")
    cx, cy = W / 2, H / 2
    if footprint:  # Vexcel structure outline, WKT in lon/lat
        from shapely import wkt
        g = wkt.loads(footprint)
        for poly in getattr(g, "geoms", [g]):
            pts = [px(y, x) for x, y in poly.exterior.coords]
            d.line(pts + pts[:1], fill=(255, 140, 0, 255), width=max(5, W // 200))
        c = g.centroid
        cx, cy = px(c.y, c.x)
    else:  # no outline: ring the CoStar point
        r = W // 14
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(255, 140, 0, 255), width=max(5, W // 200))
    if wind_from is not None:  # smoke drifts the way the wind blows TO
        a = math.radians(wind_from + 180)
        L = 0.38 * min(W, H)
        tip = (cx + L * math.sin(a), cy - L * math.cos(a))
        d.line([(cx, cy), tip], fill=(230, 230, 230, 170), width=max(10, W // 70))
        for s in (-1, 1):  # arrowhead
            b = a + math.pi + s * 0.45
            d.line([tip, (tip[0] + 0.12 * L * math.sin(b), tip[1] - 0.12 * L * math.cos(b))], fill=(230, 230, 230, 200), width=max(10, W // 70))
        txt((min(tip[0], W - 160), min(max(tip[1], 60), H - 120)), "SMOKE", big)
    for nb in neighbors:
        p = px(nb["lat"], nb["lon"])
        if inside(p):
            d.ellipse((p[0] - 10, p[1] - 10, p[0] + 10, p[1] + 10), fill=(255, 220, 0, 255), outline="black", width=2)
            txt((p[0] + 14, p[1] - 12), f'{nb["name"][:18]}, {nb["stories"]} fl, sprinklers {nb["sprinklers"]}', fill=(255, 230, 90))
    for i, (_, _, _, la, lo) in enumerate(staging[:1]):
        p = px(la, lo)
        if inside(p):
            d.rectangle((p[0] - 26, p[1] - 26, p[0] + 26, p[1] + 26), outline=(0, 230, 255, 255), width=6)
            txt((p[0] - 10, p[1] - 18), "S", big, fill=(0, 230, 255))
        else:  # off the image: arrow on the edge toward it
            m, dr = staging[0][1], staging[0][2]
            k = min((W / 2 - 40) / max(abs(p[0] - W / 2), 1), (H / 2 - 70) / max(abs(p[1] - H / 2), 1))
            q = (W / 2 + (p[0] - W / 2) * k, H / 2 + (p[1] - H / 2) * k)
            txt((min(max(q[0] - 60, 4), W - 300), min(max(q[1] - 16, 60), H - 110)), f"S: {m} m {dr} (off map)", big, fill=(0, 230, 255))
    n = 0
    for m, dr, la, lo in hydrants:
        p = px(la, lo)
        if on_map(lat, lon, la, lo, half_m) and n < 6:
            n += 1
            d.ellipse((p[0] - 15, p[1] - 15, p[0] + 15, p[1] + 15), fill=(230, 30, 30, 255), outline="white", width=3)
            txt((p[0] + 18, p[1] - 14), f"H{n}", big, fill=(255, 120, 120))
    # frame: title, north arrow, scale, legend
    d.rectangle((0, 0, W, 52), fill=(0, 0, 0, 170))
    txt((12, 10), (title or "APPROACH MAP")[:60], big)
    d.polygon([(W - 40, 70), (W - 54, 108), (W - 26, 108)], fill="white")
    txt((W - 48, 110), "N", big)
    s50 = 50 / (2 * half_m) * W
    d.rectangle((14, H - 104, 14 + s50, H - 96), fill="white")
    txt((14, H - 132), "50 m")
    d.rectangle((0, H - 86, W, H), fill=(0, 0, 0, 170))
    txt((12, H - 80), "orange = target   H = hydrant   S = open lot for staging (verify)")
    txt((12, H - 46), wind_text or "wind not available")
    out = src.replace(".jpg", "_approach.jpg")
    Image.alpha_composite(im.convert("RGBA"), over).convert("RGB").save(out, quality=88)
    return out


if __name__ == "__main__":
    import tempfile
    from PIL import Image
    t = os.path.join(tempfile.mkdtemp(), "a.jpg")
    Image.new("RGB", (600, 600), (40, 60, 40)).save(t)
    o = approach_map(t, 29.76, -95.37, footprint="POLYGON((-95.3702 29.7598,-95.3698 29.7598,-95.3698 29.7602,-95.3702 29.7602,-95.3702 29.7598))",
                     hydrants=[(40, "N", 29.7604, -95.37), (500, "E", 29.76, -95.365)], staging=[(0.9, 400, "S", 29.7564, -95.37)],
                     wind_from=60, wind_text="wind from ENE 8 mph", neighbors=[{"name": "Next Door", "stories": 3, "sprinklers": "yes", "lat": 29.7597, "lon": -95.3708}])
    assert os.path.exists(o) and Image.open(o).size == (600, 600)
    assert Image.open(o).getpixel((300, 2)) != (40, 60, 40)  # title band drawn
    assert bearing(0, 0, 0.001, 0)[1] == "N" and bearing(0, 0, 0, -0.001)[1] == "W"
    lat, lon = 29.7584604, -95.3682126
    print("hydrants", hydrants(lat, lon, "Houston")[:3])
    print("roof", roof(lat, lon))
    print("staging", staging(lat, lon))
