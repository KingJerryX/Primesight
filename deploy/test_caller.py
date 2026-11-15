"""Plays a dispatcher round through the question-the-caller segment, the way a player would: hear the vague opening,
ask with a chip and by typing, note an answer, write the dispatch, send. Checks the grader hears the questions asked.
Needs the server running (python -m primesight.server); with ANTHROPIC_API_KEY the caller is live, without it scripted.

python deploy/test_caller.py [--base http://localhost:8801] [--out DIR] [--no-grade]
"""
import argparse, json, os, re, time
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:8801")
ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output", "caller_test"))
ap.add_argument("--no-grade", action="store_true", help="stop after send, don't wait for the graders")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
shot = lambda page, name: page.screenshot(path=os.path.join(a.out, f"{name}.png"))
FLOORS = {233953: "fifth", 234022: "sixth", 236914: "seventh", 233899: "thirty-sixth", 247344: "fourth"}
OTHER = re.compile(r"drill-(lineup|crew|rival)\.js")  # other segments may be mid-edit

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport={"width": 1400, "height": 860})
    errors, graded = [], {}
    page.on("pageerror", lambda e: OTHER.search(e.stack or str(e)) or errors.append(f"{e}\n{e.stack}"))
    page.on("request", lambda r: r.url.endswith("/game/grade") and graded.update(json.loads(r.post_data)))
    page.goto(f"{a.base}/home.html")
    page.click("nav a:has-text('Simulation')")
    page.wait_for_url("**/drill.html")
    page.click("#go")
    page.wait_for_selector("#caller-chips [data-caller-ask]", timeout=20_000)
    r = page.evaluate("drill.round")
    pid, word = int(r["case"].split("_")[0]), FLOORS[int(r["case"].split("_")[0])]
    lines = page.locator("#call li").all_inner_texts()
    print("round:", r["case"], r["incident"]["label"], "| opening:", lines[1])
    assert len(lines) == 2 and word not in lines[1].lower(), lines  # the call-taker's question, then the vague opening
    assert page.text_content("#kind") == "", "the incident shows before anyone asked"

    def ask(how, text):
        n = page.locator("#call li").count()
        t0 = time.time()
        if how == "chip":
            page.click(f"#caller-chips [data-caller-ask='{text}']")
        else:
            page.fill("#caller-q", text); page.press("#caller-q", "Enter")
        page.wait_for_function(f"document.querySelectorAll('#call li').length === {n + 2} && !document.querySelector('#call .caller-wait')", timeout=15_000)
        ans = page.locator("#call li").nth(n + 1).inner_text().removesuffix("NOTE").strip()
        print(f"  {time.time() - t0:.2f}s  Q: {text} -> {ans}")
        assert ans and not page.locator("#call li").nth(n + 1).evaluate("e => e.classList.contains('caller-err')"), ans
        return ans

    floor_ans = ask("chip", "What floor?")
    assert word in floor_ans.lower(), f"asked the floor, heard {floor_ans!r}"
    page.click("#call li:nth-child(4) [data-pin]")  # note the floor answer
    ask("type", "What happened?")
    assert page.text_content("#kind") == r["incident"]["label"], "asking what happened should code the call"
    ask("type", "What is your blood type?")  # not in the script: the caller must not make one up
    shot(page, "1_call")
    notes = page.locator("#notes li:not(.empty) span").all_inner_texts()
    print("notes:", notes)
    assert len(notes) == 1 and word in notes[0].lower()
    text = page.evaluate("drill.callText()")
    assert text.count("Q: ") == 3 and "What floor?" in text, text

    # Write and send; the grader must be told what the player heard, not the script
    page.click("#towrite")
    page.keyboard.type(f"{r['incident']['label']}, floor {word}, {notes[0]}.")
    shot(page, "2_write")
    page.click("#send")
    page.wait_for_selector("#grading:not([hidden])")
    page.wait_for_function("document.querySelector('#thread .msg.me')")
    assert graded.get("call") == text, graded
    if not a.no_grade:
        page.wait_for_selector("#result:not([hidden]) .score", timeout=180_000)
        shot(page, "3_result")
        print("score:", page.inner_text("#result .score").replace("\n", " "))
    assert not errors, errors
    b.close()
print("caller round played; screenshots in", os.path.abspath(a.out))
