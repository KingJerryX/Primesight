"""Plays the "Which building" tab of a dispatcher round in a headless browser, the way a player would: read the caller's
description, pick a photo, check the other screens now show that building and its address is in the notes. No grading, so
no API key needed. Needs the server running (python -m primesight.server).

python deploy/test_lineup.py [--base http://localhost:8787] [--out DIR]
"""
import argparse, os
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:8787")
ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output", "lineup_test"))
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
shot = lambda page, name: page.screenshot(path=os.path.join(a.out, f"{name}.png"))

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport={"width": 1400, "height": 860})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"{e} {e.stack or ''}"))
    page.goto(f"{a.base}/drill.html")
    for n in range(2):  # two rounds: the second checks the tab resets
        page.click("#go") if n == 0 else page.evaluate("start()")  # "Take another call" without grading
        page.wait_for_function("drill.round && document.querySelectorAll('.lineup-card img').length === 3", timeout=20_000)
        r = page.evaluate("drill.round")
        pid = int(r["case"].split("_")[0])
        tabs = page.locator("#tabs button").all_inner_texts()
        print("round:", r["case"], r["incident"]["label"], "| tabs", [t.split("\n")[0] for t in tabs])
        assert tabs[0].startswith("Which building") and page.locator("#v-lineup").is_visible()
        assert "Locate" in " ".join(tabs)
        clue = page.inner_text("#lineup-clue")
        print("clue:", clue)
        assert len(clue) > 20 and "Asking" not in clue
        page.wait_for_function("[...document.querySelectorAll('.lineup-card img')].every(i => i.complete && i.naturalWidth > 0)", timeout=20_000)
        cands = page.evaluate("[...document.querySelectorAll('.lineup-card')].map(e => e.innerText)")
        assert [c.strip().lower() for c in cands] == ["building a", "building b", "building c"], cands
        shot(page, f"{n + 1}_lineup")

        # Round 1 picks a decoy first, then the right one; the confirmation never says which is right.
        srcs = page.evaluate("[...document.querySelectorAll('.lineup-card img')].map(i => i.getAttribute('src'))")
        wrong_pid = next(int(s.split("/img/")[1].split("_")[0]) for s in srcs if f"/img/{pid}_" not in s)
        for target in ([wrong_pid, pid] if n == 0 else [pid]):
            idx = page.evaluate("""t => { const cs = [...document.querySelectorAll('.lineup-card')];
              return cs.findIndex(e => e.querySelector('img').getAttribute('src').endsWith('/img/' + t + '_aerial.jpg')); }""", target)
            page.click(f".lineup-card >> nth={idx}")
            page.wait_for_function(f"drill.building && drill.building.pid === {target}", timeout=15_000)
            page.wait_for_function("document.querySelector('#lineup-picked').textContent.startsWith('Selected')")
            said = page.inner_text("#lineup-picked")
            print("picked:", said)
            assert said.startswith(f"Selected {'ABC'[idx]}: ") and "right" not in said.lower() and "wrong" not in said.lower()
            assert page.locator(f".lineup-card >> nth={idx}").get_attribute("class").endswith("lineup-on")
            assert page.locator(".lineup-card.lineup-on").count() == 1
            addr = page.evaluate("drill.building.address.replace(/, [A-Z]{2}$/, '')")
            assert said.endswith(addr), (said, addr)
            notes = page.locator("#notes li .use").all_inner_texts()
            assert addr in notes, (addr, notes)
            # The picked building opens from above and from each side, like the first responder screen
            assert page.locator("#lineup-viewer").is_visible() and page.locator("#lineup-strip button").count() == 5
            for side in ("north", "east", "south", "west"):
                page.click(f"#lineup-strip [data-view='{side}']")
                page.wait_for_function(f"""() => {{ const i = document.querySelector('#lineup-big img');
                  return (i.complete && i.naturalWidth > 0 && i.src.endsWith('/aerial/{target}/{side}.jpg')) || document.querySelector('.lineup-miss'); }}""", timeout=30_000)
                ok = page.evaluate("!document.querySelector('.lineup-miss')")
                print(f"  {side}: {'photo' if ok else 'no photo from this side'}")
                if n == 0 and target == pid and side == "east":
                    shot(page, "4_side_east")
            page.click("#lineup-strip [data-view='top']")
        if n == 0:
            shot(page, "2_picked")
            page.locator("#tabs button[data-v='record']").click()
            rec = page.inner_text("#record")
            assert addr.split(",")[0] in rec, rec
            shot(page, "3_record")
            page.locator("#tabs button[data-v='lineup']").click()

    mine = [e for e in errors if "drill-lineup" in e or not any(f"drill-{s}" in e for s in ("caller", "crew", "rival"))]
    print("page errors:", errors or "none")
    assert not mine, mine
    b.close()
print("lineup test pass")
