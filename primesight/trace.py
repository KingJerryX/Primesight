"""Per-run step log for the projector: one JSON line per step. cache/trace/<run_id>.jsonl on a laptop;
the Supabase events table when deployed (store.remote), so a projector on another instance sees every step."""
import json, os, time
from . import paths, store

DIR = os.path.join(paths.DATA, "cache", "trace")
_t0 = {}


def log(run_id, step, detail=""):
    if not run_id:
        return
    t0 = _t0.setdefault(run_id, time.time())
    write(run_id, json.dumps({"t": round(time.time() - t0, 2), "step": step, "detail": detail}) + "\n")


def write(run_id, lines):
    """Append already-formatted JSON lines (one step, or a recorded run being replayed)."""
    if store.remote:
        return store.extend("trace:" + run_id, [json.loads(line) for line in lines.splitlines() if line.strip()])
    os.makedirs(DIR, exist_ok=True)
    with open(os.path.join(DIR, f"{run_id}.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(lines)


def read(run_id):
    if store.remote:
        return "".join(json.dumps(s) + "\n" for s in store.read("trace:" + run_id))
    p = os.path.join(DIR, f"{run_id}.jsonl")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""
