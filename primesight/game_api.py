"""Routes for the emergency-awareness game.

GET /game/config          -> {"sdk_key"}: the Matterport Showcase SDK key. It is a browser key by design (Matterport
                             limits it to the domains on its allow list), so the game page fetches it from here
                             instead of the key living in a committed file.
GET /game/scan/<model>    -> our saved scan points for a Matterport model: id, xy, rotation, SAM 3 hits
GET /game/levels          -> [{"id", "name", "floor", "points", "explore_s", "questions", "checked"}], smallest floor first
GET /game/level/<model>   -> one level as deploy/build_levels.py wrote it, answers included (solo play grades in the page)
"""
import json, os, re

from . import paths

VISION = os.path.join(paths.DATA, "cache", "vision")
LEVELS = os.path.join(paths.DATA, "cache", "levels")


def levels():
    out = []
    for n in sorted(os.listdir(LEVELS)) if os.path.isdir(LEVELS) else []:
        if n.endswith(".json"):
            lv = json.load(open(os.path.join(LEVELS, n), encoding="utf-8"))
            out.append({k: lv[k] for k in ("id", "name", "floor", "points", "explore_s", "checked")} | {"questions": len(lv["questions"])})
    return sorted(out, key=lambda lv: lv["points"])


def handle(path):
    """(status, body, content type) for a game route, or None if the path isn't one."""
    if path == "/game/levels":
        return 200, levels(), "application/json"
    m = re.fullmatch(r"/game/level/([A-Za-z0-9]{11})", path)
    if m:
        p = os.path.join(LEVELS, m.group(1) + ".json")
        return (200, json.load(open(p, encoding="utf-8")), "application/json") if os.path.exists(p) else (404, {"error": "no such level"}, "application/json")
    if path == "/game/config":
        key = os.environ.get("MATTERPORT_SDK_KEY", "").strip()
        return (200, {"sdk_key": key}, "application/json") if key else (503, {"error": "MATTERPORT_SDK_KEY is not set"}, "application/json")
    m = re.fullmatch(r"/game/scan/([A-Za-z0-9]{11})", path)
    if m:
        p = os.path.join(VISION, m.group(1) + ".json")
        return (200, json.load(open(p, encoding="utf-8")), "application/json") if os.path.exists(p) else (404, {"error": "no saved scan"}, "application/json")
    return None


if __name__ == "__main__":
    assert handle("/game/scan/../x") is None and handle("/buildings") is None
    assert handle("/game/scan/1Gu1PWojCPH")[0] == 200 and handle("/game/scan/AAAAAAAAAAA")[0] == 404
    ls = handle("/game/levels")[1]
    assert ls and [lv["points"] for lv in ls] == sorted(lv["points"] for lv in ls)
    assert handle(f"/game/level/{ls[0]['id']}")[1]["questions"] and handle("/game/level/AAAAAAAAAAA")[0] == 404
    print("game_api ok")
