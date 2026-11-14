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


def detect(proc, model, img):
    import torch
    hits = []
    for label in LABELS:
        x = proc(images=img, text=label, return_tensors="pt").to("cuda")
        x = {k: (v.half() if v.dtype == torch.float32 else v) for k, v in x.items()}
        with torch.no_grad():
            o = model(**x)
        r = proc.post_process_instance_segmentation(o, threshold=THRESHOLD, mask_threshold=0.5,
                                                    target_sizes=x["original_sizes"].tolist())[0]
        hits += [{"label": label, "score": round(s, 2), "box": [round(v) for v in b]}
                 for s, b in zip(r["scores"].tolist(), r["boxes"].tolist())]
    return hits


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
        for j, url in enumerate(s["faces"]):
            img = Image.open(io.BytesIO(requests.get(url, timeout=60).content)).convert("RGB").resize((SIDE, SIDE))
            hits = detect(proc, model, img)
            if hits:
                draw = ImageDraw.Draw(img)
                for h in hits:
                    draw.rectangle(h["box"], outline="red", width=4)
                    draw.text((h["box"][0], max(0, h["box"][1] - 12)), f'{h["label"]} {h["score"]}', fill="red")
                img.save(os.path.join(img_dir, f"{i:03}_{j}.jpg"), quality=85)
            found += [{**h, "face": j} for h in hits]
        sweeps.append({"id": s["id"], "index": i, "xy": s["xy"], "hits": found})
    counts = {lab: sum(any(h["label"] == lab for h in s["hits"]) for s in sweeps) for lab in LABELS}
    out = {"model_id": mid, "name": d["name"], "floor": floor, "sweeps": sweeps,
           "sweeps_with": counts, "seconds": round(time.time() - t0)}
    json.dump(out, open(path, "w", encoding="utf-8"), indent=1)
    return out


if __name__ == "__main__":
    pids = [int(a) for a in sys.argv[1:]] or [233899]
    proc, model = load()
    for pid in pids:
        mids = costar.property_ids_with_scans().get(pid, [])
        for m in matterport.models(mids):
            try:
                r = scan(proc, model, m["id"], m["floor"])
                print(pid, m["id"], f'floor {m["floor"]}', len(r["sweeps"]), "sweeps", r["sweeps_with"], f'{r["seconds"]}s', flush=True)
            except Exception as e:  # one bad scan (expired URL, empty model) shouldn't stop the batch
                print(pid, m["id"], "FAILED", repr(e)[:150], flush=True)
