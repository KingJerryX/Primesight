"""SAM 3 over Matterport panoramas: find exit signs, stairs, elevators and extinguishers at each scan point.

Runs in the GPU env, ahead of time (results land in cache/vision/<model_id>.json; resumable):
HF_HUB_OFFLINE=1 ~/miniconda3/envs/sam3_env/python.exe -m primesight.vision 233899 [more property ids]
"""
import io, json, os, sys, time
import requests
from PIL import Image, ImageDraw
from . import costar, matterport

OUT = os.path.join(costar.CACHE, "vision")  # gitignored: derived from Matterport imagery
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


def near_exit(sweeps, m=4):
    """Ids of scan points standing within ~m metres of an exit sign."""
    return [s["id"] for s in sweeps if any(h["label"] == "exit sign" and kept(h) and metres(h) <= m for h in s["hits"])]


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


if __name__ == "__main__":
    assert colored(Image.new("RGB", (40, 20), (200, 30, 30)), [0, 0, 40, 20]) == 1.0
    assert colored(Image.new("RGB", (40, 20), (40, 40, 40)), [0, 0, 40, 20]) == 0.0
    if sys.argv[1:] == ["--verify"]:
        sys.exit(verify_saved())
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
