"""The team's review of the graders' playbook ideas (grader.queue writes them to the grader:suggestions stream).

Only for us: every route needs PRIMESIGHT_REVIEW_KEY (in .env and the Vercel env; not the judges' passcode). Without
the key set the routes answer 404, so the deploy shows nothing. The page is demo/variants/review.html, linked from nowhere.
POST /review/list    {"key"}                                   -> {"ideas": [{"i", "date", "incident", "case", "what", "why", "state"}]}
POST /review/decide  {"key", "i", "state": accepted|dismissed|pending, "what"?}  -> the same list after the decision

Decisions are an append-only stream (grader:decisions); the newest one per idea wins. Accepted ideas reach Primesight
through lessons(), which agent.run() adds to each call it briefs.
"""
import hmac, os

from . import store

STATES = ("accepted", "dismissed", "pending")
J = "application/json"


def board(ideas, decisions):
    """Each idea with its newest decision; an accepted idea may carry the reviewer's edited wording."""
    out = [{"i": i, **d, "state": "pending"} for i, d in enumerate(ideas)]
    for d in decisions:
        if isinstance(d.get("i"), int) and 0 <= d["i"] < len(out) and d.get("state") in STATES:
            out[d["i"]]["state"] = d["state"]
            if d.get("what"):
                out[d["i"]]["what"] = d["what"]
    return out


def ideas():
    return board(store.read("grader:suggestions"), store.read("grader:decisions"))


def lessons():
    """Accepted ideas as a block for the agent's prompt, grouped by incident type; "" when there are none."""
    try:
        ok = [d for d in ideas() if d["state"] == "accepted"]
    except Exception:  # the store is down: brief without them
        return ""
    if not ok:
        return ""
    by = {}
    for d in ok:
        by.setdefault(d["incident"], []).append(d["what"])
    return "Playbook additions the team approved (use the ones that fit this call):\n" + "\n".join(
        f"- {inc.replace('_', ' ')}: {w}" for inc, ws in sorted(by.items()) for w in dict.fromkeys(ws))


def _allowed(key):
    want = os.environ.get("PRIMESIGHT_REVIEW_KEY", "")
    return bool(want) and isinstance(key, str) and hmac.compare_digest(key.encode(), want.encode())


def post(path, body):
    if path not in ("/review/list", "/review/decide"):
        return None
    if not os.environ.get("PRIMESIGHT_REVIEW_KEY"):
        return 404, {"error": "not found"}, J
    if not _allowed(body.get("key")):  # ponytail: no lockout; the key is long and random, add a rate limit if it isn't
        return 403, {"error": "wrong key"}, J
    if path == "/review/decide":
        i, state, what = body.get("i"), body.get("state"), body.get("what")
        n = len(store.read("grader:suggestions"))
        if not (isinstance(i, int) and 0 <= i < n and state in STATES) or (what is not None and not (isinstance(what, str) and 0 < len(what.strip()) <= 600)):
            return 400, {"error": "i must be an idea's index, state one of " + ", ".join(STATES) + ", what a short text"}, J
        store.append("grader:decisions", {"i": i, "state": state, **({"what": what.strip()} if what else {})})
    return 200, {"ideas": ideas()}, J


if __name__ == "__main__":
    a = [{"incident": "fall", "what": "say the height"}, {"incident": "fall", "what": "say the height"}, {"incident": "smoke", "what": "x"}]
    b = board(a, [{"i": 0, "state": "accepted"}, {"i": 2, "state": "accepted"}, {"i": 2, "state": "dismissed"},
                  {"i": 1, "state": "accepted", "what": "say the fall height"}, {"i": 9, "state": "accepted"}, {"i": 0, "state": "bogus"}])
    assert [d["state"] for d in b] == ["accepted", "accepted", "dismissed"] and b[1]["what"] == "say the fall height", b
    os.environ["PRIMESIGHT_REVIEW_KEY"] = "k" * 32
    assert post("/review/list", {"key": "nope"})[0] == 403 and post("/review/list", {})[0] == 403 and post("/other", {}) is None
    assert _allowed("k" * 32) and not _allowed(None) and not _allowed("k" * 31)
    os.environ["PRIMESIGHT_REVIEW_KEY"] = ""
    assert post("/review/list", {"key": ""})[0] == 404
    print("review checks pass")
