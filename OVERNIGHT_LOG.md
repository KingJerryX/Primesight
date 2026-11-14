# Overnight log (Nov 14, 02:15 to ~07:15 EST)

Running notes for the morning summary. Claude API budget: $25.

## Spend
| Time | What | Cost |
|---|---|---|
| 02:40 | sms.py self-check accidentally ran the live agent (load_dotenv refilled the popped key) | $0.35 |
| 03:30-03:45 | Live batch 1: 8 calls, all 6 categories (now instant replays) | $4.10 |
| 03:50 | Live batch 2: re-recorded active threat + gas leak, new types solar / battery / heat | $2.90 |
| 04:00 | Phone flow follow-up (live /ask) + live batch 3: vehicle into building at night, unknown odor, Saturday smoke in LA | $2.03 |
| 04:20 | Live batch 4: two fire calls with the new exposures layer | $1.44 |
| 04:50 | Live batch 5: EV parking garage fire, recorded 3 times while fixing what it exposed | $1.69 |
| | **Total so far** | **~$12.55 of $25** |

## Done
- 02:20 Ranked the kit's buildings (`primesight/rank.py` -> cache/ranked.json): 60 office buildings, Houston and LA, one scan each (the floor with the most tenants). CoStar records for all 60 cached.
- 02:25 SAM 3 queue switched to one scan per ranked building (log: cache/vision_run3.txt).
- 02:30 Read the Photon 2.0 hackathon guidelines (Notion) and the Examples page. Prize needs: built on Photon 2.0, live on at least one channel (iMessage is home base). Judged on vision, craft, depth (real-world context, edge cases), traction. "Thoughtful safety and fallback behaviors", "context that persists across time, channels and teammates".
- 02:40 `primesight/sms.py`: one conversation handler for both the Photon bridge and a phone simulator (dispatch -> ack + briefing + images; follow-ups -> /ask; NOTE -> building note; failures still answer).
- 02:55 Server: POST /sms + GET /sms/<sim-id> (phone simulator), GET /latest (projectors follow the newest run), /buildings (60 ranked, SAM 3 status, tenant floors), /incidents, demo pages at /<name>.html, "mode": "draft" on /brief (no LLM), PRIMESIGHT_HOST for LAN access.
- 03:00 demo/variants/dispatch-cad.html: CAD console (search over 60 buildings, floors marked scanned/tenants, 26 incident chips by category, time of call, people, caller, free text, live dispatch preview, Randomize, Fast draft). Tested: random CO alarm at MEDPLACE floor 18 drafted in ~30 s with clinic tenants, hydrants, staging, nearest scan floor 16.
- 03:05 demo/variants/phone.html: iMessage-style simulator on the same conversation code as Photon. Tested with replays: help -> dispatch -> ack -> briefing -> floorplan + aerial -> follow-up answer.
- 03:10 Three more dispatch consoles: dispatch-judge.html (3 columns: building / floor stack / emergency grid, "Surprise me", texts land on phone.html?id=judge), dispatch-radio.html (free-text CAD narrative, autocomplete for addresses and incident phrases with Tab, live parse of building / floor / type / category using the server's keyword rule), dispatch-map.html (Leaflet map of all 60 buildings, green = SAM 3 done, Houston/LA jump, floor + category + type).
- 03:40 Five projector views, all following the newest run from any console or the phone (commit 76af01e):
  - projector-timeline: steps in time order, helper steps indented, briefing lines tagged by data source.
  - projector-graph: the real deepagents layout (agent, 3 subagents, tools, /buildings/ /scans/ /playbook/, grader, phone); nodes and edges light up per step.
  - projector-layers: one column per data source (CoStar, Matterport + SAM 3, Vexcel + hydrants, crew memory) filling as the agent pulls them; briefing lines color-coded by source.
  - projector-stack: the building floor by floor (bar = people per floor, incident floor pulsing), the marked floor plan, and the crew's phone.
  - projector-ops: time to text, steps, helpers, grader PASS/REVISE, cost, texts queued for staff, tenant flags.
  - Replays now animate at the recorded pace; `?speed=3` plays faster.
- 03:40 demo hub at http://localhost:8787/ linking every console, projector and phone, with live counts (buildings, SAM 3 floors, tenants, emergency types, playbooks, agent mode).

- 03:28 Photon 2.0 bridge (`primesight/photon_bridge.py`, commits 0d2daa9, dcee5fd): polls received messages, replies on space.id / receivedBy.id with parentMessageId threading, typing indicator, image upload as multipart/related, shared-line `register` command. Starts inside the server when PHOTON_API_KEY + PHOTON_PROJECT_ID are set. `selftest` runs a full round trip against a local mock (paging, dedupe, group-chat skip, typing, upload). Morning steps in PHOTON_WALKTHROUGH.md.
- 03:30 Second SAM 3 worker started from the far end of the queue (cache/vision_run4_reverse.txt); finished scans are skipped, so the two meet in the middle.
- 03:35 outside-scout subagent (commit aad172c): `outside_now` tool gives live wind from Open-Meteo (keyless), every hydrant within 300 m, roof data. Fire and hazmat skills now carry a WIND/WATER line; wind is no longer NOT KNOWN. Graph projector shows the 4th subagent.
- 03:40 Live batch of 8 calls across all six categories (cache/live_batch1.txt). inside-scout runs live (walk_to), all graded "satisfied" on the first five.
- 03:45 Catalog now 31 types: rooftop/solar fire (Vexcel roof solar flag), lithium battery fire, vehicle into building, heat illness (live temperature), unknown odor. Fixed keyword order bugs: "Electrical fire on the 5th floor" was matching structure_fire ("fire on"), "heat stroke" would have matched stroke, "solar panel" would have matched electrical ("panel"). 11 classification asserts in the server self-test.

- 03:50 Live batch 2 (cache/live_batch2.txt): the profiling line is gone from the active-threat briefing; gas leak now carries "WIND: from NNE 5.4 mph, gas drifts SSW"; solar fire cross-checked layers itself ("No solar panels seen in 2022 aerial"); battery fire used 4 subagents in parallel (outside-scout, inside-scout, tenant-risk, notify-writer).
- 03:55 Los Angeles buildings had 0 hydrants (only Houston had a layer). The LA County Fire layers return 0 inside the City of LA; the City's DWP layer (maps.lacity.org Fire_Department/MapServer/0) works: 12-65 hydrants within 300 m. Heat illness and unknown odor calls now go to outside-scout too (temperature, wind).
- 04:00 Research: AED detection with SAM 3 does not work. "AED cabinet" boxes any wall cabinet (top hit: a keypad safe at 0.96), "AED" hit a fire-extinguisher cabinet. AED stays NOT KNOWN; a false AED is worse than none. RESEARCH_EMERGENCIES.md (local, not in repo): all 31 types with layers and needs, plus the live briefings.
- Building notes: every live test run appends a "crew note" to cache/buildings/<pid>.md, and later briefings cite them ("same report logged ... may be a repeat call"). Before the demo these test notes get archived to cache/buildings_test_archive/ (moved, not deleted).
- 04:00 Judge mode: time of call and free details; AGENTS.md treats headcounts as daytime figures. Live batch 3 (cache/live_batch3.txt): "PEOPLE: ~31 by daytime headcount; call at 02:00, likely far fewer on site"; LA hydrants now show (550 Hill St: 37 m SW); unknown odor carries a WIND line; the Saturday smoke call noted the ground-floor gym may be busy.
- 04:00 Phone simulator flow checked end to end: replayed dispatch -> ack, briefing, floor plan, aerial; then a live follow-up "which way is the wind pushing the smoke?" answered on the same building with a fresh wind reading and the hydrant list.
- 04:00 SAM 3 is 2.2x faster with the same output: each face is encoded once (`get_vision_features`) and the 4 text prompts run on the cached features (`vision_embeds=`). 12 test faces: 42.2 s -> 19.1 s, identical boxes on all 12. Measured later over 1119 scan points: 5.85 s per point per worker including downloads, about 2.9 s with two workers sharing the GPU. Both workers restarted at 03:59 (forward and reverse down the ranked list); 16 of 60 buildings done. Comment added on the earlier throughput note.
- 04:20 New layer, exposures: outside-scout now lists kit buildings within 100 m (distance, direction, stories, sprinklers) and flags one in the smoke drift. 22 of the 60 ranked buildings have one. Live batch 4 (cache/live_batch4.txt, $1.44): "NEXT TO: Brentwood Court 35 m SW, 3 stories, sprinklered, close to WSW smoke drift"; Westside Towers East names its twin 50 m W. The kit is a sample, so the line says "kit records only".
- 04:50 New type: parking garage / EV fire (32 types). CoStar parking now reaches the agent (2764 of 3339 properties: covered/surface spaces, parking elevators). Live run: "PARKING: 360 covered + 120 surface spaces; 1 parking elevator". Its first version showed three problems, all fixed: a lookup note in the header ("data run as floor -2"), "NEXT TO: no buildings within 100 m" (the kit can't prove absence; the field is now left out when empty), and ~1517 people on floor 1 because one tenant's employee count was company-wide (1505 staff in 4535 sqft). Headcounts are now capped at leased sqft / 100: 106 of 2572 tenants in the 60 buildings were over, total -8%. Older recordings shift by tens of people only, left as is.
- 04:50 SAM 3 passed the target: 51 of 60 ranked buildings scanned; workers finishing the last 9.
- 05:15 All 60 ranked buildings scanned. Ranked 30 more (top 90; the first 60 unchanged) and started two workers on them; the consoles now list 90 buildings.
- 05:50 Pre-morning cleanup: the 30 test crew notes written tonight were moved to cache/buildings_test_archive/ (only the 3 notes from Nov 13 stay on 233899). Morning summary filled in (MORNING_SUMMARY.md, local).
- 05:53 SAM 3 finished all 90 ranked buildings (target was 50), no failures.
- 05:57 Ranked 30 more (top 120, first 90 unchanged); two workers started on them.

## Errors and fixes
- Live active-threat run labelled an advocacy organization "possible target (from name only)". Profiling by religion/ethnicity/nationality/politics is now forbidden in AGENTS.md (always in the prompt), tenant-risk's prompt and the security skill (commit 82aae86). That run's recording gets deleted and re-recorded.
- I accidentally started a second server on 8787 while the batch ran (Windows let both bind). Killed it by PID within a minute.
- load_dotenv refills a popped env var: set ANTHROPIC_API_KEY="" (not pop) to force the free path in self-checks. Commented on the dotenv note.
- CARTO dark_all tiles now say "API KEY REQUIRED" -> OSM tiles + CSS invert filter. ChatOverflow note posted.
- Projectors showed nothing: `/trace` is `application/x-ndjson`, the fetch helper's `ct.includes("json")` sent it to `r.json()`, and the poller's catch swallowed the SyntaxError. Fixed with `startsWith("application/json")`. ChatOverflow note posted.
- `phone.html?id=judge` returned 404: BaseHTTPRequestHandler.path includes the query string. Split it before routing.
- Trace args are cut at 200 chars, so `JSON.parse` on them fails: stepLabel now pulls single fields by regex (subagent name, note, queued text).
- After a grader revision hits max_iterations, the agent's final message can open with a paragraph explaining its changes ("I couldn't confirm the hydrant distances..."), and that paragraph would have been texted to the crew. run() now cuts everything before the "PRIMESIGHT |" header for briefings (crew replies are left alone); self-check asserts added; the one affected recording was trimmed.
