"""Dispatcher round segment: the crew texts back. While the graders run, Engine 1 asks one follow-up a crew en route
would ask, picked for the emergency category and this building's data; the player answers from their notes.

GET  /drill/crew/<case>/<incident>                     -> {"qid", "question"}
POST /drill/crew/check {case, incident, qid, answer}   -> {"ok", "expected", "why", "reply", "by": "code"|"model"}

Stateless: the page sends back the qid it was asked. Ground truth is grader.answer_key (the same facts the grader uses).
Code decides first (numbers, yes/no, tenant names); claude-haiku-5-5 only when code can't, with a 2 s cap. No key or
no answer in time -> counted wrong, with the record shown.
"""
import json, os, random, re
from functools import lru_cache
from . import drill, grader

J = "application/json"
CASES = {f"{p}_{m}": (p, m) for p, m in drill.FLOORS}
MODEL = "claude-haiku-5-5"
NO = r"\b(no|not|none|negative|nope|without|unsprinklered|non-sprinklered)\b"
DUNNO = r"\b(unknown|don'?t know|dunno|idk|not sure|no idea)\b"
NUM_WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                        "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}


@lru_cache(maxsize=16)
def _key(case):
    pid, mid = CASES[case]
    k = grader.answer_key(pid, mid)
    rec = dict((drill.building(pid) or {}).get("record", []))
    return {**k, "pm": rec.get("Property manager")}


def _people(k):
    return sum(t["employees"] or 0 for t in k["tenants_on_floor"])


def _named(k):
    return [t["name"] for t in k["tenants_on_floor"] if not t["name"].startswith("Unknown")]


# qid -> (category, applies(key), question, expected(key), kind). kind says how code checks it:
# "yes" a yes on record, "num" a number, "pm" property manager, "names" tenant names.
QUESTIONS = {
    "freight": ("medical", lambda k: "Freight" in k["elevators"], "Which elevator takes a stretcher? Freight on record?",
                lambda k: "a freight elevator, the one that takes a stretcher", "yes"),
    "cars_med": ("medical", lambda k: isinstance(k["elevators"].get("Passenger"), int),
                 "How many passenger cars? We'll have security hold one in the lobby.",
                 lambda k: f"{k['elevators']['Passenger']} passenger elevators", "num"),
    "cars": ("rescue", lambda k: isinstance(k["elevators"].get("Passenger"), int),
             "How many passenger cars in that building? Need to find the right one.",
             lambda k: f"{k['elevators']['Passenger']} passenger elevators", "num"),
    "keys": ("rescue", lambda k: True, "Who has the elevator keys? Property manager on record?",
             lambda k: f"a property manager, {k['pm']}" if k["pm"] else "no property manager listed", "pm"),
    "sprinklers": ("fire", lambda k: k["sprinklers"] == "yes", "Sprinklered?", lambda k: "it is sprinklered", "yes"),
    "headcount": ("fire", lambda k: _people(k) > 0, "How many people on the fire floor?",
                  lambda k: f"about {_people(k)} people on floor {k['floor']}", "num"),
    "tenants": ("fire", lambda k: bool(_named(k)) and not _people(k), "Who's on the fire floor? Any tenant names?",
                lambda k: "on floor {}: {}".format(k["floor"], ", ".join(_named(k)[:4])), "names"),
    "stories": ("fire", lambda k: bool(k["stories"]), "How many stories? Deciding on the high-rise pack.",
                lambda k: f"{k['stories']} stories", "num"),
}


def _target(qid, k):
    return {"cars_med": k["elevators"].get("Passenger"), "cars": k["elevators"].get("Passenger"),
            "headcount": _people(k), "stories": k["stories"]}.get(qid)


def options(case, incident):
    k, cat = _key(case), drill.INCIDENTS[incident][1]
    return [q for q, (c, applies, *_) in QUESTIONS.items() if c == cat and applies(k)]


def code_check(qid, k, answer):
    """True / False when code can tell, None when it can't."""
    t = " ".join(answer.lower().split())
    kind, neg, dunno = QUESTIONS[qid][4], bool(re.search(NO, t)), bool(re.search(DUNNO, t))
    if kind == "num":
        nums = {int(n) for n in re.findall(r"\d+", t)} | {v for w, v in NUM_WORDS.items() if re.search(rf"\b{w}\b", t)}
        want = _target(qid, k)
        if nums:  # headcounts are estimates: within 20%; counts of cars and stories are exact
            return any(abs(n - want) <= (max(2, want * .2) if qid == "headcount" else 0) for n in nums)
        return False if neg or dunno else None
    if kind == "yes":  # "freight, count unknown" is still a yes
        if neg:
            return False
        yes = re.search(r"\b(yes|yeah|yep|yup|affirm\w*|correct|freight|sprinkler\w*|fully|on record|has one)\b", t)
        return True if yes else (False if dunno else None)
    if kind == "pm":
        if not k["pm"]:
            return False if dunno else (True if neg else None)
        if neg or dunno:
            return False
        return True if (re.search(r"\b(yes|yeah|yep|property manager|manager|pm|management)\b", t)
                        or grader._has(t, grader._tenant_word(k["pm"]))) else None
    if kind == "names":
        words = [w for w in map(grader._tenant_word, _named(k)) if w]
        return True if grader._has(t, *words) else (False if neg or dunno else None)


def model_check(question, expected, answer):
    """Haiku's call when code can't tell. None if there's no key or it takes over 2 s."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        r = anthropic.Anthropic(timeout=2.0, max_retries=0).messages.create(
            model=MODEL, max_tokens=80, thinking={"type": "disabled"}, messages=[{"role": "user", "content":
                f"A fire crew asked dispatch: {question!r}\nThe building record says: {expected}\nDispatch replied: {answer!r}\n"
                'Does the reply give the crew the right answer? Plain words for the same thing count. Reply with JSON only: {"ok": true or false, "why": "<8 words>"}'}])
        out = json.loads(re.search(r"\{.*\}", next(b.text for b in r.content if b.type == "text"), re.S).group(0))
        return bool(out["ok"]), str(out.get("why", ""))[:120]
    except Exception:
        return None


def check(case, incident, qid, answer):
    k = _key(case)
    _, _, question, expected, _ = QUESTIONS[qid]
    exp = expected(k)
    if not answer.strip():
        return {"ok": False, "expected": exp, "why": "no reply before the crew moved on", "reply": "No reply. We'll find out on scene.", "by": "code"}
    ok, by, why = code_check(qid, k, answer), "code", ""
    if ok is None:
        m = model_check(question, exp, answer)
        ok, by, why = (m[0], "model", m[1]) if m else (False, "code", "couldn't match the reply to the record")
    why = why or ("matches the record" if ok else "doesn't match the record")
    return {"ok": ok, "expected": exp, "why": why, "reply": "Copy." if ok else f"Negative, records show {exp}.", "by": by}


def handle(path):
    m = re.fullmatch(r"/drill/crew/(\d+_\w{11})/([a-z_]{1,40})", path)
    if not m:
        return None
    case, incident = m.groups()
    if case not in CASES or incident not in drill.INCIDENTS:
        return 400, {"error": "unknown case or incident"}, J
    opts = options(case, incident)
    if not opts:
        return 404, {"error": "no follow-up for this round"}, J
    qid = random.choice(opts)
    return 200, {"qid": qid, "question": QUESTIONS[qid][2]}, J


def post(path, body):
    if path != "/drill/crew/check":
        return None
    case, incident, qid, answer = (body.get(f) for f in ("case", "incident", "qid", "answer"))
    if case not in CASES or incident not in drill.INCIDENTS or qid not in QUESTIONS or not isinstance(answer, str) \
            or qid not in options(case, incident):
        return 400, {"error": "bad case, incident, qid or answer"}, J
    return 200, check(case, incident, qid, answer[:300]), J


if __name__ == "__main__":
    os.environ.pop("ANTHROPIC_API_KEY", None)  # checks run without the model
    assert handle("/drill/caller/x") is None and post("/drill/other", {}) is None
    assert handle("/drill/crew/1_abcdefghijk/fall")[0] == 400 and handle("/drill/crew/../x") is None
    for case in CASES:
        for inc in drill.INCIDENTS:
            assert options(case, inc), (case, inc)
            s, q, _ = handle(f"/drill/crew/{case}/{inc}")
            assert s == 200 and q["qid"] in options(case, inc)
    wf, lp = "233899_jUnF84zghQk", "236914_PfDGxZeDnkz"  # Wells Fargo: 16 people on 36; 1225 Loop: 21 on 7, no PM
    c = lambda case, inc, qid, a: post("/drill/crew/check", {"case": case, "incident": inc, "qid": qid, "answer": a})[1]
    assert c(wf, "cardiac_arrest", "freight", "Yes, freight elevator on record")["ok"]
    assert not c(wf, "cardiac_arrest", "freight", "No freight")["ok"] and c(wf, "fall", "freight", "freight, count unknown")["ok"]
    assert not c(wf, "fall", "freight", "don't know")["ok"]
    assert c(wf, "structure_fire", "headcount", "about 16 people")["ok"] and not c(wf, "smoke", "headcount", "50")["ok"]
    assert c(wf, "smoke", "stories", "71 stories")["ok"] and not c(wf, "smoke", "stories", "36")["ok"]
    assert c(lp, "fall", "cars_med", "four passenger cars")["ok"] and not c(lp, "fall", "cars_med", "6")["ok"]
    assert c(lp, "elevator_entrapment", "keys", "none on record")["ok"] and not c(lp, "elevator_entrapment", "keys", "the big guy")["ok"]
    assert c(wf, "elevator_entrapment", "keys", "property manager, yes")["ok"]
    r = c(wf, "fall", "freight", "")
    assert not r["ok"] and r["reply"].startswith("No reply") and r["expected"]
    r = c(wf, "fall", "freight", "the one in back maybe")  # code can't tell, no model -> wrong, with the record
    assert not r["ok"] and r["reply"].startswith("Negative, records show") and r["by"] == "code"
    assert post("/drill/crew/check", {"case": wf, "incident": "fall", "qid": "headcount", "answer": "16"})[0] == 400  # not a medical question
    assert post("/drill/crew/check", {"case": wf, "incident": "fall", "qid": "freight", "answer": 5})[0] == 400
    nb = "234022_Fv5Q2P8Kmsf"
    assert "tenants" in options(nb, "structure_fire") and c(nb, "structure_fire", "tenants", "Crystal CoWorks and a tax office")["ok"]
    print("crew checks pass")
