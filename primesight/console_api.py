"""Routes for the dispatch console (Lane B): a building's 3D scans by floor, and Vexcel aerial views on demand.

GET /scans/<pid>               -> [{"id", "name", "floor"}], every Matterport scan of the building (floor is null if the name doesn't say)
GET /aerial/<pid>/<view>.jpg   -> Vexcel image of the building; view is top, north, east, south or west
"""
import json, os, re, tempfile
from concurrent.futures import ThreadPoolExecutor
from . import costar, matterport, outside

TMP = os.path.join(tempfile.gettempdir(), "primesight-console")  # outside the repo, so sponsor imagery never lands in git
_ids = _ranked = None


def scans(pid):
    global _ids
    path = os.path.join(TMP, f"scans_{pid}.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    if _ids is None:
        _ids = costar.property_ids_with_scans()

    def one(mid):  # a scan that fails to load is left out rather than failing the building
        try:
            return matterport.models([mid])
        except Exception:
            return []
    with ThreadPoolExecutor(8) as ex:
        out = [s for found in ex.map(one, _ids.get(pid, [])) for s in found]
    os.makedirs(TMP, exist_ok=True)
    json.dump(out, open(path, "w", encoding="utf-8"))
    return out


def aerial(pid, view):
    """Path of the cached image, fetching it from Vexcel the first time. None if we have no location or Vexcel has no image."""
    global _ranked
    path = os.path.join(TMP, f"{pid}_{view}.jpg")
    if os.path.exists(path):
        return path
    if _ranked is None:
        _ranked = {r["pid"]: r for r in json.load(open(os.path.join(costar.CACHE, "ranked.json"), encoding="utf-8"))}
    b = _ranked.get(pid)
    if not b or not b.get("lat"):
        return None
    try:
        return outside.ortho(b["lat"], b["lon"], path) if view == "top" else outside.oblique(b["lat"], b["lon"], path, side=view)
    except Exception:
        return None


def handle(path):
    """(status, body, content type) for a console route, or None if the path isn't one."""
    m = re.fullmatch(r"/scans/(\d{1,10})", path)
    if m:
        return 200, scans(int(m.group(1))), "application/json"
    m = re.fullmatch(r"/aerial/(\d{1,10})/(top|north|east|south|west)\.jpg", path)
    if m:
        p = aerial(int(m.group(1)), m.group(2))
        return (200, open(p, "rb").read(), "image/jpeg") if p else (404, {"error": "no aerial image for this view"}, "application/json")
    return None


if __name__ == "__main__":
    assert handle("/buildings") is None and handle("/aerial/1/../top.jpg") is None and handle("/aerial/1/up.jpg") is None
    print(len(scans(233899)), "scans for 233899;", aerial(233899, "top"))
