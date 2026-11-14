# Overnight log (Nov 14, 02:15 to ~07:15 EST)

Running notes for the morning summary. Claude API budget: $25.

## Spend
| Time | What | Cost |
|---|---|---|
| 02:40 | sms.py self-check accidentally ran the live agent (load_dotenv refilled the popped key) | $0.35 |

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

## Errors and fixes
- load_dotenv refills a popped env var: set ANTHROPIC_API_KEY="" (not pop) to force the free path in self-checks. Commented on the dotenv note.
- CARTO dark_all tiles now say "API KEY REQUIRED" -> OSM tiles + CSS invert filter. ChatOverflow note posted.
- Projectors showed nothing: `/trace` is `application/x-ndjson`, the fetch helper's `ct.includes("json")` sent it to `r.json()`, and the poller's catch swallowed the SyntaxError. Fixed with `startsWith("application/json")`. ChatOverflow note posted.
- `phone.html?id=judge` returned 404: BaseHTTPRequestHandler.path includes the query string. Split it before routing.
- Trace args are cut at 200 chars, so `JSON.parse` on them fails: stepLabel now pulls single fields by regex (subagent name, note, queued text).
