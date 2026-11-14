"""Level builder for the emergency-awareness game: one Matterport scan -> cache/levels/<model>.json, plus clean photos and
the floor plan in output/ (served at /img/<name>.jpg).

  python deploy/build_levels.py <model> [<model> ...]          build levels (needs the Matterport keys in .env)
  python deploy/build_levels.py <model> --start <scan label>   pick the start point by hand
  python deploy/build_levels.py                                self-check only

Every answer comes from data we already have: SAM 3 signs merged per physical sign (vision.locate), the walking graph between
scan points (matterport.route) and the floor plan's frame (briefing.plan_px). A person still checks each level before it
ships ("checked": false until they do): SAM 3 also fires on name plates and intercoms.

Directions: a cube face j of a scan point looks along heading yaw - 90 j degrees (counter-clockwise from east, model y up;
fitted in vision.ray, and checked by eye against Showcase). Showcase opens at a point with ss=<scan label + 1> and faces
heading h with sr=0,<radians(h - 90)> (both checked with the SDK on 29 of 29 points).
"""
import io, json, math, os, random, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from primesight import briefing, matterport, paths, vision  # noqa: E402

LEVELS = os.path.join(paths.DATA, "cache", "levels")
SIDE = 1024  # photo size: the faces come at 2048
BINS = [(10, "under 10 m"), (25, "10 to 25 m"), (50, "25 to 50 m"), (math.inf, "over 50 m")]
SIDES = ["left", "right", "ahead", "behind"]


def explore_seconds(points):
    """Explore time: 15 s + 1.5 s per scan point, kept within 20-120 s (the plan's formula; levels can override it)."""
    return int(min(120, max(20, round(15 + 1.5 * points))))


def side(rel):
    """Where a heading `rel` degrees from the way you face points: ahead within 45 degrees, behind past 135, else left or right
    (counter-clockwise is left: model y points up)."""
    r = (rel + 180) % 360 - 180
    return "ahead" if abs(r) <= 45 else "behind" if abs(r) >= 135 else "left" if r > 0 else "right"


def face_heading(q, j):
    return math.degrees(vision.yaw(q)) - 90 * j


def sr(heading):
    """Showcase start rotation for facing `heading`: camera yaw = heading - 90 degrees, in radians, in (-pi, pi]."""
    return f"0,{math.radians((heading - 90 + 180) % 360 - 180):.3f}"


def bearing_out(xy):
    """Heading (degrees) of a walk's first 2+ m from its start, so a 30 cm hop to the next point doesn't set the direction."""
    a = xy[0]
    b = next((p for p in xy[1:] if math.dist(a, p) >= 2), xy[-1])
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def count_options(n):
    lo = max(1, n - 1)
    return list(range(lo, lo + 4))


def bin_of(m):
    return next(label for limit, label in BINS if m < limit)


def _photo(url):
    import requests
    from PIL import Image
    for i in range(3):
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
            im.thumbnail((SIDE, SIDE))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
            return buf.getvalue()
        except Exception:
            time.sleep(2 * (i + 1))
    raise RuntimeError("face image did not download")


def build(mid, start_label=None):
    import networkx as nx
    v = json.load(open(os.path.join(paths.DATA, "cache", "vision", f"{mid}.json"), encoding="utf-8"))
    d = matterport.detail(mid)  # face and plan URLs are signed and expire: download right away
    seen = {s["id"]: s for s in v["sweeps"]}
    sweeps = [{**s, **{k: seen[s["id"]][k] for k in ("q", "hits") if k in seen.get(s["id"], {})}} for s in d["sweeps"]]
    pos = {s["id"]: s["xy"] for s in sweeps}
    by_id = {s["id"]: s for s in sweeps}
    signs = vision.locate(sweeps, labels=("exit sign",))
    exits = vision.near_exit(sweeps)
    pulls = vision.near_exit(sweeps, label="fire alarm pull station")
    g = matterport.walk_graph(sweeps)
    ends = [e for e in exits if e in g]
    if not ends:
        raise ValueError(f"{mid}: no located exit sign near a scan point")
    to_exit = nx.multi_source_dijkstra_path_length(g, set(ends))  # the part of the floor connected to an exit
    rng = random.Random(mid)  # the same scan always builds the same level

    if start_label is not None:
        start = next(s["id"] for s in sweeps if str(s["label"]) == str(start_label))
    else:  # far enough from an exit that the way out has to be remembered, not a dead-end corner
        inner = sorted((n for n in to_exit if n not in exits), key=to_exit.get)
        start = inner[int(0.7 * (len(inner) - 1))] if inner else ends[0]
    head0 = face_heading(by_id[start]["q"], 0)
    walk = matterport.route(sweeps, start, ends)["exit"]
    from_start = nx.single_source_dijkstra_path_length(g, start)

    def sign_near(xy, located):
        return min(located, key=lambda s: math.dist(s["xy"], xy))

    def frac(xy):
        if not plan:
            return None
        x, y = briefing.plan_px(xy, d["floorplan_frame"], plan["px"][1])
        return [round(x / plan["px"][0], 4), round(y / plan["px"][1], 4)]

    plan = None
    if d["floorplan_url"]:
        from PIL import Image
        import requests
        im = Image.open(io.BytesIO(requests.get(d["floorplan_url"], timeout=60).content)).convert("RGB")
        res = d["floorplan_frame"]["res"]
        plan = {"img": f"level_{mid}_plan.jpg", "px": list(im.size), "w_m": round(im.size[0] / res, 1), "h_m": round(im.size[1] / res, 1)}
        im.thumbnail((1600, 1600))
        im.save(os.path.join(briefing.OUT, plan["img"]), quality=85)

    def save_face(sid, j, data):
        name = f"level_{mid}_{by_id[sid]['label']}_{j}.jpg"
        open(os.path.join(briefing.OUT, name), "wb").write(data)
        return name

    def busiest(sid, faces):
        """The face with the most detail (largest JPEG): a blank wall compresses small."""
        return max(faces, key=lambda j: len(faces[j]))

    questions = []
    # 1. A photo of a spot, then: which way to the nearest exit? The answer is the walk's first direction against the photo's.
    cands = sorted(n for n in to_exit if n not in exits and n != start and 6 <= to_exit[n] <= 35 and math.dist(pos[n], pos[start]) >= 5)
    if cands:
        p = rng.choice(cands)
        out = matterport.route(sweeps, p, ends)["exit"]
        faces = {j: _photo(u) for j, u in enumerate(by_id[p]["faces"])}
        answers = {j: side(bearing_out(out["xy"]) - face_heading(by_id[p]["q"], j)) for j in faces}
        pool = [j for j in faces if answers[j] != "ahead"] or list(faces)  # an exit straight ahead is a giveaway
        j = busiest(p, {k: faces[k] for k in pool})
        questions.append({"id": "exit_dir", "type": "choice", "scenario": "fire", "photo": save_face(p, j, faces[j]),
                          "prompt": "Fire alarm. You are standing here. Which way to the nearest exit?",
                          "options": SIDES, "answer": answers[j], "why": f"{out['m']:.0f} m walk, starting {answers[j]}",
                          "route": [frac(xy) for xy in out["xy"]] if plan else None})
    # 2. The turns from the start to the nearest exit, when there are 1 to 3 of them.
    turns = [t for _, t in walk["legs"] if t]
    if 1 <= len(turns) <= 3:
        questions.append({"id": "exit_turns", "type": "sequence", "scenario": "fire", "options": ["left", "right"], "answer": turns,
                          "prompt": "From where you started, which turns take you to the nearest exit?",
                          "why": " then ".join(f"{m:.0f} m" + (f" after a {t}" if t else "") for m, t in walk["legs"])})
    if plan:
        tau = max(5.0, math.hypot(plan["w_m"], plan["h_m"]) / 10)
        # 4. Pin the exit nearest the start.
        sign = sign_near(walk["xy"][-1], signs)
        questions.append({"id": "exit_pin", "type": "pin", "scenario": "smoke", "tau_m": round(tau, 1), "answer": frac(sign["xy"]),
                          "prompt": "Smoke in the corridor. Drop a pin on the exit closest to where you started.",
                          "route": [frac(xy) for xy in walk["xy"]]})
        # 5. Where was this photo taken? A spot in the middle distance from the start, so it was likely walked past.
        mid_pts = sorted((n for n in from_start if n not in (start,) and n in to_exit), key=from_start.get)
        if mid_pts:
            w = rng.choice(mid_pts[len(mid_pts) * 2 // 5: max(len(mid_pts) * 4 // 5, len(mid_pts) * 2 // 5 + 1)])
            faces = {j: _photo(u) for j, u in enumerate(by_id[w]["faces"])}
            j = busiest(w, faces)
            questions.append({"id": "where", "type": "pin", "scenario": None, "tau_m": round(tau, 1), "photo": save_face(w, j, faces[j]),
                              "prompt": "Where was this photo taken?", "answer": frac(pos[w])})
        # 7. Pin the nearest fire alarm pull station, by walking distance.
        pull_ends = [n for n in pulls if n in g]
        reach = matterport.route(sweeps, start, pull_ends)["exit"] if pull_ends else None
        if reach:
            ps = sign_near(reach["xy"][-1], vision.locate(sweeps, labels=("fire alarm pull station",)))
            questions.append({"id": "pull_pin", "type": "pin", "scenario": "alarm", "tau_m": round(tau, 1), "answer": frac(ps["xy"]),
                              "prompt": "There's smoke but no alarm. Drop a pin on the nearest fire alarm pull station.",
                              "why": f"{reach['m']:.0f} m from where you started"})
    # 6. How many exits does this floor have? (signs merged per physical sign; a curator confirms the count)
    questions.append({"id": "exit_count", "type": "choice", "scenario": None, "options": count_options(len(signs)), "answer": len(signs),
                      "prompt": "How many exit signs are on this floor?"})
    # 8. How far was the nearest exit from the start?
    questions.append({"id": "exit_far", "type": "choice", "scenario": "fire", "options": [b for _, b in BINS], "answer": bin_of(walk["m"]),
                      "prompt": "How far did you have to walk from your start to the nearest exit?", "why": f"{walk['m']:.0f} m"})

    level = {"id": mid, "model": mid, "name": v.get("name"), "floor": v.get("floor"), "points": len(sweeps),
             "explore_s": explore_seconds(len(sweeps)), "checked": False, "built": time.strftime("%Y-%m-%d"),
             "start": {"point": start, "label": by_id[start]["label"], "heading": round(head0, 1),
                       "ss": int(by_id[start]["label"]) + 1, "sr": sr(head0), "frac": frac(pos[start])},
             "plan": plan, "exits": [{"frac": frac(s["xy"]), "seen_from": s["seen_from"]} for s in signs],
             "questions": questions}
    os.makedirs(LEVELS, exist_ok=True)
    json.dump(level, open(os.path.join(LEVELS, f"{mid}.json"), "w", encoding="utf-8"), indent=1)
    return level


if __name__ == "__main__":
    assert [explore_seconds(n) for n in (3, 10, 37, 261)] == [20, 30, 70, 120]
    assert [side(a) for a in (0, 44, 90, -90, 180, -170, 400)] == ["ahead", "ahead", "left", "right", "behind", "behind", "ahead"]
    assert count_options(1) == [1, 2, 3, 4] and count_options(5) == [4, 5, 6, 7]
    assert bin_of(9.9) == "under 10 m" and bin_of(10) == "10 to 25 m" and bin_of(80) == "over 50 m"
    assert round(bearing_out([(0, 0), (0.3, 0), (0, 3)])) == 90  # the short hop east doesn't count; the walk goes north
    q13 = [0.013242218643426895, -0.007554785814136267, 0.9924874305725098, -0.12139353901147842]  # 1Gu1PWojCPH, scan 13
    h13 = face_heading(q13, 0)  # Showcase showed this face at sr=0,1.815 (camera yaw 104 degrees)
    assert round(h13) == -166 and abs(float(sr(h13).split(",")[1]) - 1.815) < 0.005, sr(h13)
    print("build_levels ok")
    args = sys.argv[1:]
    start = args[args.index("--start") + 1] if "--start" in args else None
    for m in [a for i, a in enumerate(args) if a != "--start" and (i == 0 or args[i - 1] != "--start")]:
        lv = build(m, start)
        print(m, lv["name"], f"floor {lv['floor']}", f"{lv['points']} points, {lv['explore_s']} s,",
              [q["id"] for q in lv["questions"]], f"start ss={lv['start']['ss']} sr={lv['start']['sr']}")
