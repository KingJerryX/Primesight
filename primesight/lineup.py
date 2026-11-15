"""Dispatcher round segment "Which building is it?": the caller describes the building from the street, the player picks
it out of three Vexcel aerials (the right one and two decoys of similar height in the same city).

GET /drill/lineup/<case>  -> {"clue", "candidates": [{"pid", "name" (or null), "address", "stories", "image"}]}, shuffled, the right
                             one not marked. image is /img/<pid>_aerial.jpg (output/, committed and deployed).

The clue and decoys are made once per case and committed in cache/lineup/<case>.json, so a round never waits on a model:
python -m primesight.lineup build   (claude-sonnet-5-5 looks at the three aerials and the CoStar records)
"""
import base64, glob, io, json, os, random, re, sys
from . import costar
from .drill import FLOORS

DIR = os.path.join(costar.ROOT, "cache", "lineup")
OUT = os.path.join(costar.ROOT, "output")
CASES = {f"{p}_{m}": p for p, m in FLOORS}
MODEL = "claude-sonnet-5-5"


def card(pid):
    b = costar.building(pid)
    addr = re.sub(r",\s*[A-Z]{2}$", "", re.sub(r"\s+,", ",", b["address"]))
    return {"pid": pid, "name": b["name"], "address": addr, "stories": b["stories"],
            "image": f"/img/{pid}_aerial.jpg"}


def decoys(pid, n=2):
    """The n buildings with an aerial in output/ in the same city, closest in height."""
    me = costar.building(pid)
    city = lambda b: b["address"].split(",")[-2].strip().lower()
    pool = []
    for p in glob.glob(os.path.join(OUT, "*_aerial.jpg")):
        q = int(os.path.basename(p).split("_")[0])
        try:
            b = costar.building(q)
        except (OSError, KeyError, ValueError):
            continue
        if q != pid and city(b) == city(me) and b["stories"]:
            pool.append((abs((b["stories"] or 1) / (me["stories"] or 1) - 1), q))
    return [q for _, q in sorted(pool)[:n]]


def round_for(case):
    pid = CASES[case]
    path = os.path.join(DIR, f"{case}.json")
    if os.path.exists(path):
        c = json.load(open(path, encoding="utf-8"))
        clue, others = c["clue"], c["decoys"]
    else:  # ponytail: not built yet, so a plain clue from the record; run the builder for a real one
        b = costar.building(pid)
        clue, others = f"It's an office building, about {b['stories']} stories.", decoys(pid)
    cands = [card(p) for p in [pid, *others]]
    random.shuffle(cands)
    return {"clue": clue, "candidates": cands}


def handle(path):
    m = re.fullmatch(r"/drill/lineup/(\d{1,10}_\w{11})", path)
    if not m:
        return None
    if m.group(1) not in CASES:
        return 404, {"error": "unknown case"}, "application/json"
    return 200, round_for(m.group(1)), "application/json"


def _jpeg(pid, px=768):
    from PIL import Image
    im = Image.open(os.path.join(OUT, f"{pid}_aerial.jpg")).convert("RGB")
    im.thumbnail((px, px))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


def _facts(pid):
    b = costar.building(pid)
    return f"{b['stories']} stories, {b['construction'] or 'unknown'} frame, built {b['year_built'] or 'unknown'}, {b['type'] or 'office'}"


def build(case):
    """Ask the model for the caller's description of the right building, written to tell it apart from the decoys."""
    import anthropic
    from dotenv import load_dotenv
    load_dotenv(os.path.join(costar.ROOT, ".env"))
    pid = CASES[case]
    others = decoys(pid)
    content = []
    for label, p in [("TARGET", pid)] + [(f"DECOY {i + 1}", q) for i, q in enumerate(others)]:
        content += [{"type": "text", "text": f"{label}: {_facts(p)}. Aerial photo, straight down, about 240 m across, the building is in the centre:"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _jpeg(p)}}]
    content.append({"type": "text", "text": (
        "A frightened 911 caller is standing on the street outside the TARGET building. Write what they say when asked what "
        "the building looks like: one or two short spoken sentences, plain words, no building name, no street name or number. "
        "Translate what the aerial shows into what someone at street level sees (how tall, shape, colour, what's right next "
        "to it: a parking lot or garage, a plaza, trees, a freeway). Pick details that tell the TARGET apart from both "
        "DECOYS, which a dispatcher will compare from above. Reply with the caller's words only, no quotes.")})
    r = anthropic.Anthropic().messages.create(model=MODEL, max_tokens=200, messages=[{"role": "user", "content": content}])
    clue = r.content[0].text.strip().strip('"')
    os.makedirs(DIR, exist_ok=True)
    out = {"case": case, "pid": pid, "decoys": others, "clue": clue, "model": MODEL}
    json.dump(out, open(os.path.join(DIR, f"{case}.json"), "w", encoding="utf-8"), indent=1)
    print(case, others, r.usage.input_tokens, "in", r.usage.output_tokens, "out |", clue)
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["build"]:
        for c in CASES:
            build(c)
        sys.exit()
    assert handle("/drill/nope") is None and handle("/drill/lineup/../x") is None
    assert handle("/drill/lineup/1_aaaaaaaaaaa")[0] == 404
    for c, pid in CASES.items():
        s, r, _ = handle(f"/drill/lineup/{c}")
        ps = [x["pid"] for x in r["candidates"]]
        assert s == 200 and r["clue"] and len(ps) == 3 == len(set(ps)) and pid in ps, (c, r)
        assert all(os.path.exists(os.path.join(OUT, f"{p}_aerial.jpg")) for p in ps)
        assert all(x["address"] and x["image"] == f"/img/{x['pid']}_aerial.jpg" for x in r["candidates"])
    assert 233953 not in decoys(247344) and 247344 not in decoys(233899)  # same city only
    print("lineup checks pass")
