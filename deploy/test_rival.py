"""Plays one dispatcher round through to the result screen and checks the Beat Primesight block (drill-rival.js):
scores side by side, Primesight's text, a verdict that matches the numbers, and no page errors from drill-rival.js.
Needs the server running and the round's cache/rival/<case>_<incident>.json built (python -m primesight.rival build).

python deploy/test_rival.py [--base http://localhost:8804] [--out DIR]
"""
import argparse, os, re
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:8804")
ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output", "rival_test"))
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport={"width": 1400, "height": 900})
    errors = []  # other segments' files may be mid-edit: only ours (and the page itself) count
    page.on("pageerror", lambda e: errors.append(str(e.stack or e)))
    page.goto(f"{a.base}/drill.html")
    page.click("#go")
    page.wait_for_function("drill.round", timeout=20_000)
    r = page.evaluate("drill.round")
    pid = int(r["case"].split("_")[0])
    floor = {233953: 5, 234022: 6, 236914: 7, 233899: 36, 247344: 4}[pid]
    print("round:", r["case"], r["incident"]["id"])
    street = page.evaluate(f"allB.then(l => l.find(x => x.pid === {pid}).address)")
    page.locator("#tabs button[data-v='locate']").click()
    page.fill("#q", street.split(" ", 1)[1])
    page.click(f"[data-pick='{pid}']")
    page.wait_for_function(f"drill.building && drill.building.pid === {pid}")
    page.click("#towrite")
    page.fill("#msg", f"{r['incident']['label']}, floor {floor}, {street}. Caller on scene, details to follow.")
    page.click("#send")
    page.wait_for_selector("#result:not([hidden]) .score", timeout=240_000)
    page.wait_for_selector("#rival", timeout=10_000)
    page.locator("#rival").scroll_into_view_if_needed()
    page.screenshot(path=os.path.join(a.out, "rival_result.png"), full_page=False)
    page.locator("#rival").screenshot(path=os.path.join(a.out, "rival_block.png"))
    tot = page.locator("#rival tfoot td").all_inner_texts()
    you, them = int(tot[1]), int(tot[2])
    verdict = page.inner_text("#rival .rival-verdict")
    print("you", you, "primesight", them, "|", verdict)
    assert page.locator("#rival tbody tr").count() >= 3
    assert re.fullmatch(rf"You beat Primesight by {you - them}", verdict) if you > them else \
        re.fullmatch(rf"Primesight wins by {them - you}", verdict) if you < them else verdict == "A tie with Primesight"
    assert page.inner_text("#rival pre >> nth=1").startswith("PRIMESIGHT")
    assert page.locator("#rival").evaluate("(s) => s.nextElementSibling && s.nextElementSibling.id") == "again"
    # A call with no cached run: the block stays away, and no error surfaces.
    n = page.evaluate("api('/drill/rival', {case: 'nope', incident: 'fall'}).then(() => 0, e => +e.message.match(/\\d{3}$/)[0])")
    assert n == 400, n
    mine = [e for e in errors if "drill-rival.js" in e or not re.search(r"drill-(caller|lineup|crew)\.js", e)]
    assert not mine, mine
    if errors:
        print("ignored errors from other segments:", len(errors) - len(mine))
    b.close()
print("rival round played; screenshots in", os.path.abspath(a.out))
