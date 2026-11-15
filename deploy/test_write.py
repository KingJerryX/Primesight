"""The Write step: click a note to paste it into the dispatch, Ctrl+Z takes it back, Enter sends, and a floor written as
a word counts. Needs the server (python -m primesight.server) and ANTHROPIC_API_KEY for the graders.

python deploy/test_write.py [--base http://localhost:8787] [--out DIR]
"""
import argparse, os
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:8787")
ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output", "write_test"))
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport={"width": 1400, "height": 860})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{a.base}/drill.html?case=234022_Fv5Q2P8Kmsf&incident=cardiac_arrest")  # Northbelt, floor 6
    page.click("#go")
    page.wait_for_function("drill.round && document.querySelectorAll('#call li').length >= 2")
    # Two notes: the address from Locate, the elevators from the record
    page.locator("#tabs button[data-v='locate']").click()
    page.fill("#q", "Sam Houston")
    page.click("[data-pick='234022']")
    page.wait_for_function("drill.building && drill.building.pid === 234022")
    page.click("#picked [data-pin] >> nth=0")
    page.locator("#tabs button[data-v='record']").click()
    page.click("#record [data-pin^='elevators']")
    notes = page.locator("#notes li .use").all_inner_texts()
    print("notes:", notes)

    # Type the opening by hand, floor as a word, then click both notes in
    page.click("#towrite")
    page.keyboard.type("Cardiac arrest, sixth floor")
    page.click("#notes li .use >> nth=0")
    page.click("#notes li .use >> nth=1")
    msg = page.input_value("#msg")
    print("after clicking notes:", repr(msg))
    assert msg.startswith("Cardiac arrest, sixth floor. ") and notes[0] in msg and msg.rstrip().endswith(".")
    assert page.locator("#v-write").is_visible()

    page.keyboard.press("Control+z")  # takes the last note back out
    undone = page.input_value("#msg")
    print("after Ctrl+Z:", repr(undone))
    assert notes[1][1:] not in undone and notes[0] in undone, undone
    page.keyboard.press("Control+Shift+z")  # and redo brings it back
    print("after redo:", repr(page.input_value("#msg")))

    page.keyboard.press("Shift+Enter")  # a new line, not a send
    assert "\n" in page.input_value("#msg") and page.locator("#grading").is_hidden()
    page.keyboard.type("Not breathing, CPR in progress.")
    page.screenshot(path=os.path.join(a.out, "1_write.png"))
    page.keyboard.press("Enter")  # sends
    page.wait_for_selector("#grading:not([hidden])", timeout=5000)
    print("Enter sent the message")
    page.wait_for_selector("#result:not([hidden]) .score", timeout=180_000)
    page.screenshot(path=os.path.join(a.out, "2_result.png"))
    floor = [c for c in page.locator("#result li").all_inner_texts() if c.lower().startswith("floor")]
    print("score:", page.inner_text("#result .score").replace("\n", " "), "| floor check:", floor)
    assert not errors, errors
    b.close()
print("write step test passed")
