"""Plays one dispatcher round in a headless browser, the way a player would: find the building, pin facts, send, and
wait for the graders. Needs the server running (python -m primesight.server) and ANTHROPIC_API_KEY for the graders.

python deploy/test_drill.py [--base http://localhost:8787] [--category fire] [--out DIR]
"""
import argparse, os
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:8787")
ap.add_argument("--category", help="re-deal until the round is this category (fire, medical, rescue)")
ap.add_argument("--demo", help="case/incident: open the demo link drill.html?case=..&incident=.. instead of a random call")
ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output", "drill_test"))
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
shot = lambda page, name: page.screenshot(path=os.path.join(a.out, f"{name}.png"))

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport={"width": 1400, "height": 860})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    if a.category:  # the server deals at random: ask again until the category comes up
        def deal(route):
            for _ in range(40):
                r = route.fetch()
                if r.json()["incident"]["category"] == a.category:
                    return route.fulfill(response=r)
            route.fulfill(response=r)
        page.route("**/drill/start*", deal)
    if a.demo:
        case, inc = a.demo.split("/")
        page.goto(f"{a.base}/drill.html?case={case}&incident={inc}")
    else:
        page.goto(f"{a.base}/home.html")  # the way a player gets there: the Game link in the nav
        page.click("nav a:has-text('Simulation')")
        page.wait_for_url("**/drill.html")
    page.click("#go")
    page.wait_for_function("drill.round && document.querySelectorAll('#call li').length >= 2", timeout=20_000)
    r = page.evaluate("drill.round")
    pid, cat = int(r["case"].split("_")[0]), r["incident"]["category"]
    print("round:", r["case"], r["incident"]["label"], "| screens", r["screens"])
    if a.demo:
        assert f'{r["case"]}/{r["incident"]["id"]}' == a.demo, r
    assert [t.inner_text().split("\n")[0] for t in page.locator("#tabs button").all()] and \
        page.locator("#tabs button").count() == len(r["screens"]) + 2  # + Which building, Write dispatch
    assert "outside" not in r["screens"] and ("tenants" in r["screens"]) == (cat == "fire")

    # Locate: search the street, pick the right building, note its address
    street = page.evaluate(f"allB.then(l => l.find(x => x.pid === {pid}).address)")
    page.locator("#tabs button[data-v='locate']").click()  # the round opens on Which building
    page.fill("#q", street.split(" ", 1)[1])
    page.click(f"[data-pick='{pid}']")
    page.wait_for_function(f"drill.building && drill.building.pid === {pid}")
    page.click("#picked [data-pin] >> nth=0")
    shot(page, "1_locate")

    # Notes from the record: elevators and sprinklers. Notes must not touch the message.
    floor = {233953: 5, 234022: 6, 236914: 7, 233899: 36, 247344: 4}[pid]
    page.locator("#tabs button[data-v='record']").click()
    for k in ("elevators", "sprinklers"):
        btn = page.locator(f"#record [data-pin^='{k}']")
        if btn.count():
            btn.click()
    # Floor plan: the map alone, no exit-sign marks
    page.locator("#tabs button[data-v='plan']").click()
    page.fill("#pfloor", str(floor)); page.click("#pshow")
    page.wait_for_function("document.querySelector('#planbox img') || /No 3D scan/.test(document.querySelector('#pnote').textContent)", timeout=60_000)
    page.wait_for_timeout(800)
    assert page.locator("#planbox .dot, #signs").count() == 0
    shot(page, "2_plan")
    if cat == "fire":
        page.locator("#tabs button[data-v='tenants']").click()
        page.fill("#tfloor", str(floor))
        if page.locator("#tenants [data-pin]").count():
            page.click("#tenants [data-pin] >> nth=-1")  # the headcount line
    notes = page.locator("#notes li:not(.empty) span").all_inner_texts()
    print("notes:", notes)
    assert notes and page.input_value("#msg") == "" and page.locator("#send").is_disabled()

    # Write it by hand, the way a player would: the emergency, the location, then the notes that matter
    page.click("#towrite")
    assert page.locator("#v-write").is_visible()
    keep = [n for n in notes if not n.startswith(("about", "3D scan"))][1:]  # the address goes first, in the opening line
    page.keyboard.type(f"{r['incident']['label']}, floor {floor}, {street}. " + ". ".join(keep) + ".")
    shot(page, "3_write")
    msg = page.input_value("#msg")
    print("message:", msg)
    assert street in msg and f"floor {floor}" in msg and page.locator("#send").is_enabled()

    # Send, watch the graders, read the result
    import time; t0 = time.time()
    page.click("#send")
    page.wait_for_selector("#grading:not([hidden])")
    page.wait_for_function("document.querySelectorAll('#steps li').length >= 5", timeout=90_000)
    shot(page, "4_grading")
    page.wait_for_selector("#result:not([hidden]) .score", timeout=180_000)
    print(f"grading took {time.time() - t0:.0f} s")
    shot(page, "5_result")
    steps = page.locator("#steps li").all_inner_texts()
    print("grader steps seen:", len(steps)); [print("  ", s.replace("\n", " | ")[:150]) for s in steps]
    print("score:", page.inner_text("#result .score").replace("\n", " "))
    print("feedback:", page.locator("#feedback li").all_inner_texts())
    if page.locator("text=Beat Primesight").count():
        print("rival:", page.locator("text=/(You beat Primesight|Primesight wins|tie)/i").first.inner_text())
    assert not errors, errors
    b.close()
print("drill round played; screenshots in", os.path.abspath(a.out))
