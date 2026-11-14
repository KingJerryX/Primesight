"""Per-run step log for the projector: one JSON line per step in cache/trace/<run_id>.jsonl."""
import json, os, time

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache", "trace")
_t0 = {}


def log(run_id, step, detail=""):
    if not run_id:
        return
    os.makedirs(DIR, exist_ok=True)
    t0 = _t0.setdefault(run_id, time.time())
    with open(os.path.join(DIR, f"{run_id}.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": round(time.time() - t0, 2), "step": step, "detail": detail}) + "\n")


def write(run_id, lines):
    """Append already-formatted JSON lines (a recorded run being replayed)."""
    os.makedirs(DIR, exist_ok=True)
    with open(os.path.join(DIR, f"{run_id}.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(lines)


def read(run_id):
    p = os.path.join(DIR, f"{run_id}.jsonl")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""
