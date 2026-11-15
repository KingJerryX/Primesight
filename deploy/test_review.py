"""The team's review page: locked without the key, lists the graders' ideas with it, an accepted idea (edited) reaches
Primesight's prompt, and "Back to review" undoes it. Needs the server and PRIMESIGHT_REVIEW_KEY in .env; never prints it.

python deploy/test_review.py [--base http://localhost:8787] [--out DIR]
"""
import argparse, os, sys
from playwright.sync_api import sync_playwright

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))
from primesight.review import lessons

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:8787")
ap.add_argument("--out", default=os.path.join(ROOT, "output", "review_test"))
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
KEY = os.environ["PRIMESIGHT_REVIEW_KEY"]

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{a.base}/admin.html")
    assert page.locator("#login").is_visible() and page.locator("#list li").count() == 0  # nothing without the key
    page.fill("#key", "not-the-key"); page.click("#login button")
    page.wait_for_function("document.getElementById('err').textContent.includes('wrong')")
    assert page.locator("#list li").count() == 0
    print("wrong key: locked out")

    page.fill("#key", KEY); page.click("#login button")
    page.wait_for_selector("#filters:not([hidden])")
    tabs = page.locator("#filters button").all_inner_texts()
    print("tabs:", tabs)
    page.screenshot(path=os.path.join(a.out, "1_list.png"))
    first = page.locator("#list li[data-i] >> nth=0")
    i = int(first.get_attribute("data-i"))
    wording = "TEST (review page check): " + first.locator(".what").inner_text()
    first.locator("[data-do='edit']").click()
    page.fill(f"[data-i='{i}'] textarea", wording)
    page.click(f"[data-i='{i}'] [data-do='save']")
    page.wait_for_function(f"!document.querySelector('[data-i=\"{i}\"]')")  # left the To review list
    assert wording in lessons(), "accepted idea not in Primesight's prompt block"
    print("accepted, and it reaches Primesight's prompt")

    page.click("#filters [data-show='accepted']")
    page.screenshot(path=os.path.join(a.out, "2_accepted.png"))
    page.click(f"[data-i='{i}'] [data-do='pending']")  # undo the test decision
    page.wait_for_function(f"!document.querySelector('[data-i=\"{i}\"]')")
    assert wording not in lessons()
    print("back to review: undone")

    page.reload()  # the key is remembered for this tab only
    page.wait_for_selector("#filters:not([hidden])")
    assert not errors, errors
    b.close()
print("review test passed")
