"""Plays one dispatcher round through the crew segment (drill-crew.js): send the dispatch, wait for Engine 1's follow-up
text, answer it from the record (or let the 30 s run out with --silent), and check the result screen's Crew follow-up box.
Needs the server running; the graders need ANTHROPIC_API_KEY.

python deploy/test_crew.py [--base http://localhost:8803] [--silent] [--wrong] [--out DIR]
"""
import argparse, os, re, sys, time
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from primesight import crew  # the answer the player would read off their notes

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:8803")
ap.add_argument("--silent", action="store_true", help="don't answer: let the crew's 30 s run out")
ap.add_argument("--wrong", action="store_true", help="answer with something the record contradicts")
ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output", "crew_test"))
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
shot = lambda page, name: page.screenshot(path=os.path.join(a.out, f"{name}.png"))
OTHERS = re.compile(r"drill-(caller|lineup|rival)\.js")  # other segments may be mid-edit: their errors aren't ours

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport={"width": 1400, "height": 860})
    errors, asked = [], {}
    page.on("pageerror", lambda e: OTHERS.search(e.stack or "") or errors.append(e.stack or str(e)))

    def on_console(m):  # drill.html's emit() catches hook errors and console.error()s them
        if m.type != "error":
            return
        stacks = " ".join(str(x.evaluate("e => e && e.stack || String(e)")) for x in m.args)
        if "drill-crew" in stacks or "drill-crew" in m.text:
            errors.append(stacks or m.text)
    page.on("console", on_console)
    page.on("response", lambda r: re.search(r"/drill/crew/\d+_\w+/\w+$", r.url) and r.ok and asked.update(r.json()))

    page.goto(f"{a.base}/drill.html")
    page.click("#go")
    page.wait_for_function("drill.round", timeout=20_000)
    r = page.evaluate("drill.round")
    case, inc = r["case"], r["incident"]["id"]
    key = crew._key(case)
    print("round:", case, r["incident"]["label"])
    page.evaluate(f"drill.pick({key['property_id']})")
    page.wait_for_function(f"drill.building && drill.building.pid === {key['property_id']}")

    page.click("#towrite")
    page.keyboard.type(f"{r['incident']['label']}, floor {key['floor']}, {key['address']}. Freight elevator on record. Sprinklered.")
    t0 = time.time()
    page.click("#send")
    page.wait_for_selector("#grading:not([hidden])")
    page.wait_for_selector(".crew-reply input", timeout=15_000)
    waited = time.time() - t0
    print(f"crew asked after {waited:.1f} s:", asked)
    assert 5.5 <= waited <= 10, waited
    assert asked.get("qid") in crew.options(case, inc)
    assert page.locator("#thread .msg.it").last.inner_text() == asked["question"]
    shot(page, "1_question")

    expected = crew.QUESTIONS[asked["qid"]][3](key)
    if not a.silent:
        answer = "no idea" if a.wrong else expected  # what the player's notes say
        page.fill(".crew-reply input", answer)
        t1 = time.time()
        page.click(".crew-reply button")
        page.wait_for_function("document.querySelectorAll('#thread .msg.it:not(.dots)').length >= 2", timeout=10_000)
        print(f"crew replied in {time.time() - t1:.2f} s:", page.locator("#thread .msg.it").last.inner_text())
        shot(page, "2_reply")
    page.wait_for_selector("#result:not([hidden]) .crew-box", timeout=180_000)
    print(f"result after {time.time() - t0:.0f} s")
    box = page.inner_text(".crew-box")
    print("crew box:", box.replace("\n", " | "))
    shot(page, "3_result")
    page.locator(".crew-box").screenshot(path=os.path.join(a.out, "3_crew_box.png"))
    assert asked["question"] in box and expected in box
    assert ("Right" in box) == (not a.silent and not a.wrong), box
    assert ("no reply in 30 s" in box) == a.silent
    assert page.locator(".crew-reply").count() == 0
    assert not errors, errors
    b.close()
print("crew round played; screenshots in", os.path.abspath(a.out))
