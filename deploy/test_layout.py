"""Left panel never overlaps: after a long call (many caller questions) and many notes, the call lines, ask box, chips,
notes and Write button must not intersect, at several screen sizes. Needs the server and ANTHROPIC_API_KEY (caller).

python deploy/test_layout.py [--base http://localhost:8787] [--out DIR]
"""
import argparse, os
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:8787")
ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output", "layout_test"))
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)

OVERLAP = """() => {
  const ids = ["call", "caller-box", "notes", "towrite"].map(id => document.getElementById(id)).filter(Boolean);
  const r = ids.map(e => [e.id, e.getBoundingClientRect()]), bad = [];
  for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++) {
    const [n1, a] = r[i], [n2, b] = r[j];
    if (a.bottom > b.top + 0.5 && b.bottom > a.top + 0.5 && a.right > b.left && b.right > a.left) bad.push(n1 + "/" + n2);
  }
  const call = document.getElementById("call");  // every call line drawn inside its own box
  for (const li of call.children) { const b = li.getBoundingClientRect(), c = call.getBoundingClientRect();
    if (b.bottom > c.top && b.top < c.bottom && (b.top < c.top - 0.5 || b.bottom > c.bottom + 0.5) && call.scrollHeight <= call.clientHeight) bad.push("line outside call"); }
  const pn = document.getElementById("panel");
  if (getComputedStyle(pn).overflowY !== "visible" && pn.scrollHeight > pn.clientHeight + 1 && innerWidth > 860 && innerHeight >= 700) bad.push("panel cut off");
  if (document.documentElement.scrollWidth > innerWidth + 1) bad.push("page scrolls sideways");
  return {bad, scroll: call.scrollHeight > call.clientHeight, atEnd: call.scrollHeight - call.scrollTop - call.clientHeight < 4};
}"""

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport={"width": 1400, "height": 860})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{a.base}/drill.html?case=234022_Fv5Q2P8Kmsf&incident=cardiac_arrest")
    page.click("#go")
    page.wait_for_function("drill.round && document.querySelectorAll('#call li').length >= 2")
    for q in ["Is he breathing?", "What floor are you on?", "What does the building look like?", "Is anyone doing CPR?",
              "How old is he?", "Is the door unlocked?", "Is there a freight elevator?", "Where should the crew go?"]:
        n = page.locator("#call li").count()
        page.fill("#caller-q", q); page.click("#caller-ask")
        page.wait_for_function(f"document.querySelectorAll('#call li:not(.caller-wait)').length >= {n + 2}", timeout=60_000)
    for btn in page.locator("#call .pin").all()[:8]:  # lots of notes
        btn.click()
    ok = True
    for w, h in [(1400, 860), (1280, 720), (1920, 1080), (1024, 640), (390, 844)]:
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(300)
        page.evaluate("document.getElementById('call').scrollTop = 1e9")
        r = page.evaluate(OVERLAP)
        print(f"{w}x{h}: overlaps={r['bad'] or 'none'} call scrolls={r['scroll']} newest in view={r['atEnd']}")
        ok &= not r["bad"]
        page.screenshot(path=os.path.join(a.out, f"panel_{w}x{h}.png"))
    page.set_viewport_size({"width": 1400, "height": 860})
    page.click("#towrite")
    page.screenshot(path=os.path.join(a.out, "write_1400x860.png"))
    print("textarea width:", page.evaluate("document.getElementById('msg').getBoundingClientRect().width"))
    assert ok and not errors, errors
    b.close()
print("layout test passed")
