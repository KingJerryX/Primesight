import glob, os, time, torch
from PIL import Image, ImageDraw
from transformers import Sam3Model, Sam3Processor

OUT = os.path.join(os.path.dirname(__file__), "out")
dev = "cuda"
t = time.time()
proc = Sam3Processor.from_pretrained("facebook/sam3")
model = Sam3Model.from_pretrained("facebook/sam3", torch_dtype=torch.float16).to(dev).eval()
print(f"load {time.time()-t:.1f}s | VRAM {torch.cuda.memory_allocated()/1e9:.2f} GB")

prompts = ["exit sign", "fire extinguisher", "door", "sprinkler head"]
faces = sorted(glob.glob(f"{OUT}/sweep*_face*.jpg"))
times = []
for f in faces:
    img = Image.open(f).convert("RGB")
    hits = []
    for p in prompts:
        inputs = proc(images=img, text=p, return_tensors="pt").to(dev)
        inputs = {k: (v.half() if v.dtype == torch.float32 else v) for k, v in inputs.items()}
        torch.cuda.synchronize(); s = time.time()
        with torch.no_grad():
            o = model(**inputs)
        torch.cuda.synchronize(); times.append(time.time() - s)
        r = proc.post_process_instance_segmentation(o, threshold=0.4, mask_threshold=0.5,
                                                    target_sizes=inputs["original_sizes"].tolist())[0]
        for sc, b in zip(r["scores"].tolist(), r["boxes"].tolist()):
            hits.append((p, round(sc, 2), [round(x) for x in b]))
    print(os.path.basename(f), hits[:6])
    if hits:
        d = ImageDraw.Draw(img)
        for p, sc, b in hits:
            d.rectangle(b, outline="red", width=6); d.text((b[0], max(0, b[1] - 14)), f"{p} {sc}", fill="red")
        img.save(f"{OUT}/sam3_{os.path.basename(f)}")
times = times[1:]  # drop warm-up
print(f"per image+prompt: mean {sum(times)/len(times)*1000:.0f} ms | peak VRAM {torch.cuda.max_memory_allocated()/1e9:.2f} GB")
