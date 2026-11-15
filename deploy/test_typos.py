"""Random typos in good dispatch messages: how often does each code check still pass? No API calls."""
import os, random, re, sys
from collections import Counter
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # python deploy/test_typos.py
os.environ["ANTHROPIC_API_KEY"] = ""
from primesight.grader import checks, answer_key, FROM_CALL
from primesight import drill

GOOD = {  # a full message per incident; {a} address, {f} floor, {s} stories
    "cardiac_arrest": "Cardiac arrest, floor {f}, {a}. Not breathing, CPR in progress. Freight elevator fits a stretcher.",
    "fall": "Fall with head injury, floor {f}, {a}. Confused and bleeding. Freight elevator for the stretcher.",
    "breathing": "Breathing problem, floor {f}, {a}. Chest tight, grey and sweating. Freight elevator for the stretcher.",
    "elevator_entrapment": "Elevator entrapment, floor {f}, {a}. Four trapped, getting hot, nobody hurt. Freight elevator on record.",
    "structure_fire": "Structure fire, floor {f}, {a}. Black smoke, people still inside, about 20 people on the floor. "
                      "{s} stories, {c} frame, sprinklers yes. Freight elevator.",
    "smoke": "Smoke in building, floor {f}, {a}. Electrical burning smell, lots of people on the floor, about 20 people. "
             "{s} stories, {c} frame, sprinklers yes. Freight elevator.",
}


def typo(word, rng):
    i = rng.randrange(len(word))
    k = rng.choice(["drop", "swap", "double", "wrong"])
    if k == "drop" and len(word) > 3: return word[:i] + word[i + 1:]
    if k == "swap" and i < len(word) - 1: return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    if k == "double": return word[:i] + word[i] + word[i:]
    return word[:i] + rng.choice("aeioustrnl") + word[i + 1:]


rng = random.Random(7)
stats, fails = Counter(), Counter()
for pid, mid in drill.FLOORS:
    key = answer_key(pid, mid)
    a = f"{key['street_number']} {key['street']}"
    for inc, tpl in GOOD.items():
        msg = tpl.format(f=key["floor"], a=a, s=key["stories"], c=(key["construction"] or "steel").lower())
        base = {c["item"] for c in checks(key, inc, msg)[0] if c["ok"]}
        for n in (1, 2, 3):
            for _ in range(40):
                words = msg.split(" ")
                letters = [i for i, w in enumerate(words) if re.fullmatch(r"[A-Za-z]{4,}[.,]?", w)]  # numbers stay exact
                for i in rng.sample(letters, min(n, len(letters))):
                    w = words[i].rstrip(".,"); words[i] = typo(w, rng) + words[i][len(w):]
                t = " ".join(words)
                for c in checks(key, inc, t)[0]:
                    if c["item"] in base:
                        stats[(n, "kept")] += c["ok"]; stats[(n, "all")] += 1
                        if not c["ok"]: fails[c["item"]] += 1
for n in (1, 2, 3):
    print(f"{n} typo word(s): {stats[(n, 'kept')]}/{stats[(n, 'all')]} checks still pass ({100 * stats[(n, 'kept')] / stats[(n, 'all')]:.1f}%)")
print("checks lost to typos:", fails.most_common(8))
