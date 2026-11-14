"""Matterport Model API: pick the scan for a floor, pull its floorplan, sweep graph and panoramas."""
import os, re, time, requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
URL = "https://api.matterport.com/api/models/graph"


def gql(query, variables=None, tries=4):
    for k in range(tries):  # Wi-Fi drops (lid, power change) shouldn't kill a long batch
        try:
            r = requests.post(URL, json={"query": query, "variables": variables or {}}, timeout=60,
                              auth=(os.environ["MATTERPORT_TOKEN_ID"], os.environ["MATTERPORT_TOKEN_SECRET"]))
            break
        except requests.ConnectionError:
            if k == tries - 1:
                raise
            time.sleep(15 * (k + 1))
    r.raise_for_status()
    body = r.json()
    if body.get("errors") and not body.get("data"):
        raise RuntimeError(body["errors"][0].get("message"))
    return body["data"]


def scan_floor(name):
    """'Suite 1500' -> 15, 'Level 53' -> 53, 'Suite 625' -> 6. None if the name doesn't say."""
    m = re.search(r"(?:level|floor|fl)\s*(\d+)", name, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"suite\s*(\d{3,4})", name, re.I) or re.match(r"(\d{3,4})\s*-", name)  # "1500 - Plaza" is a suite
    return int(m.group(1)) // 100 if m else None


def models(ids):
    """Names and floors of a building's scans, one cheap query each."""
    out = []
    for mid in ids:
        name = gql("query($id:ID!){model(id:$id){name}}", {"id": mid})["model"]["name"]
        out.append({"id": mid, "name": name, "floor": scan_floor(name)})
    return out


def pick(scans, floor):
    """Exact floor if scanned, else the nearest scanned floor (same building core, likely same layout)."""
    known = [s for s in scans if s["floor"] is not None]
    if not known:
        return None, False
    best = min(known, key=lambda s: abs(s["floor"] - floor))
    return best, best["floor"] == floor


def detail(mid):
    m = gql("""query($id:ID!){model(id:$id){name
      assets{floorplans(provider:"matterport", flags:[photogramy]){format url resolution origin{x y}}}
      locations{id label neighbors position{x y z} panos{skybox(resolution:"2k"){children}}}
      mattertags{label description}
    }}""", {"id": mid})["model"]
    plans = [f for f in m["assets"]["floorplans"] if f["format"] == "jpg"]
    locs = m["locations"]
    return {
        "id": mid, "name": m["name"],
        "floorplan_url": plans[0]["url"] if plans else None,  # signed CDN URL, expires: download, don't store
        # pixel = (x - origin.x) * res, height - (y - origin.y) * res (model y points up; checked against the image)
        "floorplan_frame": {"origin": (plans[0]["origin"]["x"], plans[0]["origin"]["y"]), "res": plans[0]["resolution"]} if plans else None,
        # label = the scan number; Showcase's ss=<label + 1> opens at that point (checked on 29 of 29 points with the SDK)
        "sweeps": [{"id": l["id"], "label": l["label"], "xy": (l["position"]["x"], l["position"]["y"]), "neighbors": l["neighbors"],
                    "faces": (l["panos"][0]["skybox"]["children"][1:5] if l["panos"] else [])} for l in locs],
        "tags": [t["label"] for t in m["mattertags"] or []],
        "link": f"https://my.matterport.com/show/?m={mid}",
    }


def walk_graph(sweeps):
    """networkx graph over sweep neighbors, edges weighted by metres."""
    import math, networkx as nx
    g = nx.Graph()
    pos = {s["id"]: s["xy"] for s in sweeps}
    for s in sweeps:
        for n in s["neighbors"]:
            if n in pos:
                g.add_edge(s["id"], n, weight=math.dist(pos[s["id"]], pos[n]))
    return g


def legs(pts):
    """Polyline -> [[metres, turn]]. turn is "" for the first leg, else "left"/"right"
    (model y points up, so a counter-clockwise turn is a left)."""
    import math
    out, head = [], None
    for a, b in zip(pts, pts[1:]):
        h, m = math.atan2(b[1] - a[1], b[0] - a[0]), math.dist(a, b)
        turn = 0 if head is None else (h - head + math.pi) % (2 * math.pi) - math.pi
        if not out or abs(turn) < math.radians(50) or m < 2:  # small bends and short hops aren't turns
            out = out or [[0, ""]]
            out[-1][0] += m
        else:
            out.append([m, "left" if turn > 0 else "right"])
        head = h
    return out


def route(sweeps, start, targets):
    """Walks over the scan-point graph from `start`: to the nearest of `targets` (sweep ids) and to the farthest
    reachable point. Returns {"exit": {"m", "legs", "xy"} or None, "far": {"m", "xy"}} or None."""
    import networkx as nx
    g = walk_graph(sweeps)
    if start not in g:
        return None
    dist, paths = nx.single_source_dijkstra(g, start)
    pos = {s["id"]: s["xy"] for s in sweeps}
    reach = [t for t in targets if t in dist]
    end, far = (min(reach, key=dist.get) if reach else None), max(dist, key=dist.get)
    ex = {"m": dist[end], "legs": legs([pos[n] for n in paths[end]]), "xy": [pos[n] for n in paths[end]]} if end else None
    return {"exit": ex, "far": {"m": dist[far], "xy": [pos[n] for n in paths[far]]}}


def download(url, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(requests.get(url, timeout=60).content)
    return path


if __name__ == "__main__":
    assert scan_floor("Suite 1500") == 15 and scan_floor("Level 53") == 53 and scan_floor("Lobby") is None
    assert scan_floor("1500 - Some Plaza") == 15 and scan_floor("1000 Louisiana - Suite 3700") == 37
    assert legs([(0, 0), (5, 0), (5, 3), (2, 3)]) == [[5, ""], [3, "left"], [3, "left"]]  # y up: north after east is left
    sw = [{"id": "a", "xy": (0, 0), "neighbors": ["b"]}, {"id": "b", "xy": (4, 0), "neighbors": ["a", "c"]},
          {"id": "c", "xy": (4, -3), "neighbors": ["b"]}]
    r = route(sw, "a", ["b"])
    assert r["exit"]["m"] == 4 and r["far"]["m"] == 7 and legs(r["far"]["xy"])[1][1] == "right"
    import sys, json
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from primesight.costar import building
    b = building(233899)
    scans = models(b["matterport_models"])
    print(json.dumps(scans))
    s, exact = pick(scans, 30)
    d = detail(s["id"])
    g = walk_graph(d["sweeps"])
    print(s["name"], "exact" if exact else "nearest", "| sweeps", len(d["sweeps"]), "| graph", g.number_of_nodes(), g.number_of_edges(), "| tags", d["tags"][:5])
