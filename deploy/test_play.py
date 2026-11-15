"""Plays every level of the drill (demo/variants/play.html) in a real browser with Playwright.

    python deploy/test_play.py [--base http://localhost:8791] [--headed] [--shots DIR] [level id ...]

Needs the server running locally (the Matterport SDK key only works on localhost) with MATTERPORT_SDK_KEY in .env.
For each level it checks, through the page's window.game and the Matterport SDK:
  1. the player starts on the level's start point, facing the planned heading
  2. W walks to a neighbouring scan point (turning first if the way ahead is blocked)
  3. dragging the view turns the camera
  4. "I'm ready" blacks the screen out and removes the scan
  5. answering every question right scores on each, and the total adds up
"""
import argparse, json, os, sys, urllib.request
from playwright.sync_api import sync_playwright


def yaw_off(a, b):
    return abs((a - b + 180) % 360 - 180)


def play(page, base, lv, shots):
    out = []
    def check(name, ok, detail=""):
        out.append((name, bool(ok), detail))
        print(f"  {'ok  ' if ok else 'FAIL'} {name} {detail}")
        return ok
    def shot(tag):
        if shots:
            page.screenshot(path=os.path.join(shots, f"{lv['id']}_{tag}.png"))

    page.goto(f"{base}/play.html?level={lv['id']}")
    page.click("#go")
    page.wait_for_function("window.game.sdk && window.game.sweep && window.game.yaw !== null", timeout=90_000)
    page.wait_for_timeout(1500)  # let the opening pose settle
    g = page.evaluate("({sweep: game.sweep, yaw: game.yaw})")
    check("starts on the start point", g["sweep"] == lv["start"]["point"], f"(label {lv['start']['label']})")
    want = lv["start"]["heading"] - 90  # SDK camera yaw = our heading - 90
    check("faces the planned heading", yaw_off(g["yaw"], want) < 15, f"(yaw {g['yaw']:.0f}, want {want:.0f})")
    shot("1_start")

    # Neighbours of the start point, from the SDK's own sweep graph.
    nbrs = page.evaluate("""start => new Promise(ok => {
        const sub = game.sdk.Sweep.data.subscribe({ onCollectionUpdated(c) {
            const s = Object.values(c).find(s => s.sid === start || s.id === start);
            if (s) { ok(Object.values(s.neighbors || {})); sub.cancel(); }
        } }); })""", lv["start"]["point"])

    # Walk: W moves forward one point. If nothing is ahead, turn (A) and try again.
    page.evaluate("document.querySelector('#explore iframe').focus()")
    start = json.dumps(lv["start"]["point"])
    moved, turns = False, 0
    for turns in range(8):
        page.keyboard.press("w")
        try:  # a move starts within a moment; landing takes seconds in SwiftShader
            page.wait_for_function(f"game.moving || game.sweep !== {start}", timeout=2500)
            page.wait_for_function(f"!game.moving && game.sweep !== {start}", timeout=20_000)
            moved = True
            break
        except Exception:
            page.keyboard.down("a"); page.wait_for_timeout(600); page.keyboard.up("a")
    now = page.evaluate("game.sweep")
    check("W walks to another point", moved, f"(after {turns} turns)")
    check("the step lands on a neighbour", now in nbrs, f"({now} in {nbrs})")
    shot("2_walked")

    # Look around: drag across the view.
    y0 = page.evaluate("game.yaw")
    vw, vh = page.viewport_size["width"], page.viewport_size["height"]
    page.mouse.move(vw * .5, vh * .5); page.mouse.down()
    for k in range(1, 11):
        page.mouse.move(vw * .5 + 30 * k, vh * .5)
        page.wait_for_timeout(30)
    page.mouse.up(); page.wait_for_timeout(800)
    y1 = page.evaluate("game.yaw")
    check("dragging turns the camera", yaw_off(y0, y1) > 10, f"({y0:.0f} -> {y1:.0f})")
    check("dragging doesn't walk", page.evaluate("game.sweep") == now)

    # Time's up: the scan goes away.
    page.click("#ready")
    check("blackout", page.evaluate("game.phase") == "blackout" and page.is_visible("#black"))
    check("the scan is removed", page.evaluate("document.querySelectorAll('iframe[src*=\"matterport\"]').length") == 0)
    shot("3_blackout")

    # Answer everything right.
    for q in lv["questions"]:
        page.wait_for_function(f"game.phase === 'question' && game.question === {json.dumps(q['id'])}", timeout=10_000)
        if q.get("photo"):
            check(f"{q['id']} photo is ready", page.evaluate("(i => i.complete && i.naturalWidth > 0 && i.getBoundingClientRect().height > 50)(document.querySelector('#q-photo'))"))
        if q["type"] == "choice":
            page.click(f"#q-opts button[data-v='{q['answer']}']")
        elif q["type"] == "sequence":
            for t in q["answer"]:
                page.click(f"#q-seq [data-t='{t}']")
            page.click("#q-send")
        else:
            page.wait_for_function("document.querySelector('#q-plan img').complete && document.querySelector('#q-plan img').naturalWidth > 0")
            box = page.locator("#q-plan img").bounding_box()
            page.mouse.click(box["x"] + q["answer"][0] * box["width"], box["y"] + q["answer"][1] * box["height"])
            page.click("#q-pin")
        r = page.evaluate("game.results[game.results.length - 1]")
        check(f"{q['id']} scores", r["points"] >= (900 if q["type"] == "pin" else 500), f"({r['points']})")
        if q.get("route") or q["type"] == "pin":
            page.wait_for_function("(i => i.complete && i.naturalWidth > 0)(document.querySelector('#q-plan img'))")
            check(f"{q['id']} reveal shows the plan", page.is_visible("#q-plan") and not page.is_visible("#q-pin"))
            shot(f"4_{q['id']}")
        page.click("#q-next")
    page.wait_for_function("game.phase === 'done'")
    total = page.evaluate("game.score")
    check("total adds up", int(page.inner_text("#total")) == total == sum(r["points"] for r in page.evaluate("game.results")), f"({total})")
    shot("5_done")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8791")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots")
    ap.add_argument("levels", nargs="*")
    a = ap.parse_args()
    if a.shots:
        os.makedirs(a.shots, exist_ok=True)
    ids = a.levels or [lv["id"] for lv in json.load(urllib.request.urlopen(a.base + "/game/levels"))]
    fails = 0
    with sync_playwright() as p:
        # Matterport needs WebGL; headless Chromium gets it from SwiftShader.
        b = p.chromium.launch(headless=not a.headed, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        for mid in ids:
            lv = json.load(urllib.request.urlopen(f"{a.base}/game/level/{mid}"))
            print(f"{lv['name']} ({mid})")
            page = b.new_page(viewport={"width": 1280, "height": 800})
            page.on("pageerror", lambda e: print("  page error:", e))
            try:
                fails += sum(not ok for _, ok, _ in play(page, a.base, lv, a.shots))
            except Exception as e:
                fails += 1
                print("  FAIL", type(e).__name__, str(e).splitlines()[0])
                if a.shots:
                    page.screenshot(path=os.path.join(a.shots, f"{mid}_error.png"))
            page.close()
        b.close()
    print("all passed" if not fails else f"{fails} failed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
