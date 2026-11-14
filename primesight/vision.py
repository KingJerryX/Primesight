"""SAM 3 over Matterport panoramas: find exit signs, stairs, elevators and extinguishers at each scan point.

Runs in the GPU env, ahead of time (results land in cache/vision/<model_id>.json; resumable):
HF_HUB_OFFLINE=1 ~/miniconda3/envs/sam3_env/python.exe -m primesight.vision 233899 [more property ids]
"""
import io, json, math, os, sys, time
import requests
from PIL import Image, ImageDraw
from . import costar, matterport

OUT = os.path.join(costar.CACHE, "vision")  # <model>.json results are committed; annotated face images stay local
# "elevator door" was dropped: it fires on every door (offices, glass, restroom stalls) at 0.9+; CoStar has elevators anyway
LABELS = ["exit sign", "stairs", "fire extinguisher", "fire alarm pull station"]
THRESHOLD = 0.45
SIDE = 1024  # faces come at 2048; SAM 3 resizes to ~1008 anyway


def load():
    import torch
    from transformers import Sam3Model, Sam3Processor
    proc = Sam3Processor.from_pretrained("facebook/sam3")
    model = Sam3Model.from_pretrained("facebook/sam3", torch_dtype=torch.float16).to("cuda").eval()
    return proc, model


_TOKENS = {}  # label -> tokenized prompt on the GPU


def detect(proc, model, img):
    """Encode the image once, then run each label's text prompt on the cached vision features.
    2.2x faster than a full forward pass per label, identical boxes (checked on 12 faces)."""
    import torch
    x = proc(images=img, return_tensors="pt").to("cuda")
    with torch.no_grad():
        vis = model.get_vision_features(pixel_values=x["pixel_values"].half())
    hits = []
    for label in LABELS:
        if label not in _TOKENS:
            _TOKENS[label] = proc(text=label, return_tensors="pt").to("cuda")
        t = _TOKENS[label]
        with torch.no_grad():
            o = model(vision_embeds=vis, input_ids=t["input_ids"], attention_mask=t["attention_mask"])
        r = proc.post_process_instance_segmentation(o, threshold=THRESHOLD, mask_threshold=0.5,
                                                    target_sizes=x["original_sizes"].tolist())[0]
        hits += [{"label": label, "score": round(s, 2), "box": [round(v) for v in b]}
                 for s, b in zip(r["scores"].tolist(), r["boxes"].tolist())]
    return hits


def fetch(mid, d, i, j, tries=4):
    """One skybox face. Re-queries the model when the signed URL expired; waits out short network drops."""
    for k in range(tries):
        try:
            r = requests.get(d["sweeps"][i]["faces"][j], timeout=60)
            if r.headers.get("Content-Type", "").startswith("image"):
                return r.content, d
            d = matterport.detail(mid)  # signed URLs expire mid-scan on big models
        except requests.ConnectionError:
            if k == tries - 1:
                raise
            time.sleep(15 * (k + 1))
    raise RuntimeError(f"no image for sweep {i} face {j}")


def scan(proc, model, mid, floor):
    path = os.path.join(OUT, f"{mid}.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    d = matterport.detail(mid)  # face URLs are signed and expire: use them right away
    img_dir = os.path.join(OUT, mid)
    os.makedirs(img_dir, exist_ok=True)
    t0, sweeps = time.time(), []
    for i, s in enumerate(d["sweeps"]):
        found = []
        for j in range(len(s["faces"])):
            raw, d = fetch(mid, d, i, j)
            img = Image.open(io.BytesIO(raw)).convert("RGB").resize((SIDE, SIDE))
            hits = detect(proc, model, img)
            for h in hits:
                if h["label"] in SIGNS:
                    h["color"] = colored(img, h["box"])  # on the clean image, before boxes are drawn
            if hits:
                draw = ImageDraw.Draw(img)
                for h in hits:
                    draw.rectangle(h["box"], outline="red", width=4)
                    draw.text((h["box"][0], max(0, h["box"][1] - 12)), f'{h["label"]} {h["score"]}', fill="red")
                img.save(os.path.join(img_dir, f"{i:03}_{j}.jpg"), quality=85)
            found += [{**h, "face": j} for h in hits]
        sweeps.append({"id": s["id"], "index": i, "xy": s["xy"], "hits": found})
    out = {"model_id": mid, "name": d["name"], "floor": floor, "sweeps": sweeps,
           "sweeps_with": counts(sweeps), "seconds": round(time.time() - t0)}
    json.dump(out, open(path, "w", encoding="utf-8"), indent=1)
    return out


# Exit signs and pull stations are red or green. SAM 3 also boxes room plaques and switches as "exit sign" at 0.7+,
# and those have no red or green inside the box, so a color check removes them.
SIGNS = ("exit sign", "fire alarm pull station")
MIN_COLOR = 0.1


def colored(img, box, pad=0):
    """Share of strongly red or green pixels inside the box (pad skips a drawn outline)."""
    x0, y0, x1, y1 = max(0, box[0] + pad), max(0, box[1] + pad), box[2] - pad, box[3] - pad
    if x1 <= x0 or y1 <= y0:  # box smaller than the outline: too small to judge, so not kept
        return 0.0
    c = img.crop((x0, y0, x1, y1)).convert("RGB")
    raw = c.tobytes()
    n = len(raw) // 3
    if n == 0:
        return 0.0
    hit = 0
    for k in range(0, len(raw), 3):
        r, g, b = raw[k], raw[k + 1], raw[k + 2]
        hit += (r > 140 and r > 1.6 * g and r > 1.6 * b) or (g > 120 and g > 1.4 * r and g > 1.2 * b)
    return round(hit / n, 3)


def kept(h):
    return h["label"] not in SIGNS or h.get("color", 0) >= MIN_COLOR


WIDTH = {"exit sign": 0.3, "fire alarm pull station": 0.13, "fire extinguisher": 0.18, "stairs": 1.0}  # metres, typical


def metres(h, width=None):
    """Rough distance to a hit: a skybox face is a 90-degree pinhole view, so focal length = SIDE / 2 px.
    ponytail: assumes a typical object width (WIDTH) seen face-on; an angled one reads farther (the safe side)."""
    return (width or WIDTH.get(h["label"], 0.3)) * (SIDE / 2) / max(h["box"][2] - h["box"][0], 1)


def yaw(q):
    """Heading (radians, model frame, z up) of a pano from its rotation quaternion [x, y, z, w]."""
    x, y, z, w = q
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def ray(s, h):
    """Where a hit points from its scan point: (origin xy, heading radians, horizontal metres).
    Face j looks along pano yaw - 90 deg * j (faces go clockwise from above); right of the face centre is clockwise.
    Fitted on 3015 exit-sign sightings in 117 scans: this convention made 2164 cross-sweep pairs agree within 1 m,
    the next best of 48 made 888. Distance from box width (metres) is horizontal after the box centre's elevation."""
    x0, y0, x1, y1 = h["box"]
    u, v = (x0 + x1) / 2 - SIDE / 2, SIDE / 2 - (y0 + y1) / 2
    th = yaw(s["q"]) - math.radians(90 * h["face"]) - math.atan(u / (SIDE / 2))
    return s["xy"], th, metres(h) * math.cos(math.atan(v / (SIDE / 2)))


def locate(sweeps, labels=SIGNS, merge_m=1.5, max_m=10):
    """One entry per physical sign: [{label, xy, seen_from, sweeps, triangulated}], most-seen first.
    Each sighting is projected to a point; points of one label within merge_m are one sign. Seen from 2+ scan points
    at 15+ degrees apart, the position is the least-squares crossing of the sight lines (box-width distance is rough).
    ponytail: greedy merge, nearest sightings first; a cluster that drifts over a doorway can swallow a second sign."""
    shots = sorted(((ray(s, h), s["id"], h["label"]) for s in sweeps if "q" in s for h in s["hits"]
                    if h["label"] in labels and kept(h)), key=lambda t: t[0][2])
    groups = []
    for (o, th, d), sid, lab in shots:
        if d > max_m:
            continue
        p = (o[0] + d * math.cos(th), o[1] + d * math.sin(th))
        g = next((g for g in groups if g["label"] == lab and math.dist(g["c"], p) <= merge_m), None)
        if g is None:
            groups.append(g := {"label": lab, "pts": [], "rays": [], "c": p})
        g["pts"].append(p)
        g["rays"].append((o, th, sid))
        g["c"] = (sum(q[0] for q in g["pts"]) / len(g["pts"]), sum(q[1] for q in g["pts"]) / len(g["pts"]))
    # Second pass: box-width distance is rough (an angled sign reads farther, a near one nearer), so a weaker group whose
    # every sight line passes within 1 m of a better-seen sign, ahead of the camera, is that sign seen from farther away.
    groups.sort(key=lambda g: -len({r[2] for r in g["rays"]}))
    for i, w in enumerate(groups):
        for s in groups[:i]:
            if s.get("gone") or s["label"] != w["label"]:
                continue
            ok = True
            for o, th, _ in w["rays"]:
                dx, dy = s["c"][0] - o[0], s["c"][1] - o[1]
                t = dx * math.cos(th) + dy * math.sin(th)  # distance along the sight line
                ok &= t > 0 and abs(dx * math.sin(th) - dy * math.cos(th)) <= 1.0
            if ok:
                s["rays"] += w["rays"]
                w["gone"] = True
                break
    groups = [g for g in groups if not g.get("gone")]
    out = []
    for g in groups:
        xy, sids, tri = g["c"], sorted({r[2] for r in g["rays"]}), False
        ths = [r[1] for r in g["rays"]]
        spread = max(abs(math.remainder(a - b, 2 * math.pi)) for a in ths for b in ths)
        if len(sids) > 1 and spread >= math.radians(15):
            # minimise sum of squared distances to the sight lines: sum(I - n n^T) p = sum(I - n n^T) o
            a = b = c = e = f = 0.0
            for o, th, _ in g["rays"]:
                nx, ny = math.cos(th), math.sin(th)
                m11, m12, m22 = 1 - nx * nx, -nx * ny, 1 - ny * ny
                a, b, c = a + m11, b + m12, c + m22
                e, f = e + m11 * o[0] + m12 * o[1], f + m12 * o[0] + m22 * o[1]
            det = a * c - b * b
            if abs(det) > 1e-6:
                p = ((c * e - b * f) / det, (a * f - b * e) / det)
                if math.dist(p, xy) <= 3:  # a crossing far from every projection means the rays disagree: keep the mean
                    xy, tri = p, True
        out.append({"label": g["label"], "xy": [round(xy[0], 2), round(xy[1], 2)], "seen_from": len(sids),
                    "sweeps": sids, "triangulated": tri})
    return sorted(out, key=lambda s: -s["seen_from"])


def near_exit(sweeps, m=4, label="exit sign"):
    """Ids of the scan point closest to each located sign (an exit sign unless label says otherwise), if within m metres:
    the places a walking route can end at a sign."""
    ends = set()
    for g in locate(sweeps, labels=(label,)):
        s = min(sweeps, key=lambda s: math.dist(s["xy"], g["xy"]))
        if math.dist(s["xy"], g["xy"]) <= m:
            ends.add(s["id"])
    return [s["id"] for s in sweeps if s["id"] in ends]


def counts(sweeps):
    return {lab: sum(any(h["label"] == lab and kept(h) for h in s["hits"]) for s in sweeps) for lab in LABELS}


def verify_saved():
    """Add the color check to results saved before it existed, using the saved (boxed) images."""
    for name in os.listdir(OUT):
        if not name.endswith(".json"):
            continue
        p = os.path.join(OUT, name)
        r = json.load(open(p, encoding="utf-8"))
        for s in r["sweeps"]:
            for h in s["hits"]:
                if h["label"] in SIGNS and "color" not in h:
                    img = Image.open(os.path.join(OUT, r["model_id"], f'{s["index"]:03}_{h["face"]}.jpg'))
                    h["color"] = colored(img, h["box"], pad=5)  # the drawn outline is 4 px, inside the box
        r["sweeps_with"] = counts(r["sweeps"])
        json.dump(r, open(p, "w", encoding="utf-8"), indent=1)
        print(r["model_id"], f'floor {r["floor"]}', r["sweeps_with"])


def add_rotation():
    """Store each sweep's pano rotation quaternion [x, y, z, w] in the saved results (one Model API query per scan).
    Needed to turn 'seen on face j of sweep s' into a compass direction: each sweep's pano is turned differently."""
    for name in sorted(os.listdir(OUT)):
        p = os.path.join(OUT, name)
        if not name.endswith(".json"):
            continue
        r = json.load(open(p, encoding="utf-8"))
        if all("q" in s for s in r["sweeps"]):
            continue
        locs = matterport.gql("query($id:ID!){model(id:$id){locations{id panos{rotation{x y z w}}}}}",
                              {"id": r["model_id"]})["model"]["locations"]
        q = {l["id"]: [l["panos"][0]["rotation"][k] for k in "xyzw"] for l in locs if l["panos"]}
        for s in r["sweeps"]:
            if s["id"] in q:
                s["q"] = q[s["id"]]
        json.dump(r, open(p, "w", encoding="utf-8"), indent=1)
        print(r["model_id"], sum("q" in s for s in r["sweeps"]), "/", len(r["sweeps"]), flush=True)


if __name__ == "__main__":
    assert colored(Image.new("RGB", (40, 20), (200, 30, 30)), [0, 0, 40, 20]) == 1.0
    assert colored(Image.new("RGB", (40, 20), (40, 40, 40)), [0, 0, 40, 20]) == 0.0

    def _sweep(sid, xy, psi, signs, scale=1.0):  # scan point at xy, pano yaw psi, seeing each sign as a real hit would
        hits = []
        for sx, sy in signs:
            rel = (psi - math.atan2(sy - xy[1], sx - xy[0])) % (2 * math.pi)
            j = round(rel / (math.pi / 2)) % 4
            u = SIDE / 2 + SIDE / 2 * math.tan(math.remainder(rel - j * math.pi / 2, 2 * math.pi))
            w = scale * WIDTH["exit sign"] * (SIDE / 2) / math.dist(xy, (sx, sy))  # scale < 1: reads farther
            hits.append({"label": "exit sign", "face": j, "color": 1, "box": [u - w / 2, SIDE / 2 - w / 2, u + w / 2, SIDE / 2 + w / 2]})
        return {"id": sid, "xy": xy, "q": [0, 0, math.sin(psi / 2), math.cos(psi / 2)], "hits": hits}
    door, hall = (2.0, 3.0), (9.0, -2.0)
    sw = [_sweep("a", (0.0, 0.0), 0.3, [door]), _sweep("b", (4.0, 0.0), -1.2, [door, hall]), _sweep("c", (8.0, 0.0), 2.5, [hall])]
    found = locate(sw)
    assert len(found) == 2, found  # two signs, not four sightings
    assert all(f["seen_from"] == 2 and f["triangulated"] for f in found), found
    far = _sweep("d", (2.0, -3.0), 1.0, [door], scale=0.75)  # 6 m away but reads 8 m: projects 2 m past the door
    found = locate(sw + [far])
    assert len(found) == 2 and max(f["seen_from"] for f in found) == 3, found  # joined along its sight line
    assert any(math.dist(f["xy"], door) < 0.05 for f in found), found
    assert any(math.dist(f["xy"], door) < 0.05 for f in found) and any(math.dist(f["xy"], hall) < 0.05 for f in found), found
    assert near_exit(sw, m=3.7) == ["a", "c"] and near_exit(sw, m=2.3) == ["c"], near_exit(sw, m=3.7)  # door 3.6 m from a and b
    if sys.argv[1:] == ["--verify"]:
        sys.exit(verify_saved())
    if sys.argv[1:] == ["--rotation"]:
        sys.exit(add_rotation())
    args = sys.argv[1:] or ["233899"]  # property ids, or Matterport model ids to jump the queue
    proc, model = load()
    for a in args:
        pid = int(a) if a.isdigit() else "-"
        mids = costar.property_ids_with_scans().get(pid, []) if a.isdigit() else [a]
        for mid in mids:
            try:
                m = matterport.models([mid])[0]  # one at a time: a failed name lookup skips only this scan
                r = scan(proc, model, m["id"], m["floor"])
                print(pid, m["id"], f'floor {m["floor"]}', len(r["sweeps"]), "sweeps", r["sweeps_with"], f'{r["seconds"]}s', flush=True)
            except Exception as e:  # one bad scan (expired URL, empty model) shouldn't stop the batch
                print(pid, mid, "FAILED", repr(e)[:150], flush=True)
