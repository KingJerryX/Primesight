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

## Errors and fixes
- load_dotenv refills a popped env var: set ANTHROPIC_API_KEY="" (not pop) to force the free path in self-checks.
- 02:55 Server: POST /sms + GET /sms/<sim-id> (phone simulator), GET /latest (projectors follow the newest run), /buildings (60 ranked, SAM 3 status, tenant floors), /incidents, demo pages at /<name>.html, "mode": "draft" on /brief (no LLM), PRIMESIGHT_HOST for LAN access.
- 03:00 demo/variants/dispatch-cad.html: CAD console (search over 60 buildings, floors marked scanned/tenants, 26 incident chips by category, time of call, people, caller, free text, live dispatch preview, Randomize, Fast draft). Tested: random CO alarm at MEDPLACE floor 18 drafted in ~30 s with clinic tenants, hydrants, staging, nearest scan floor 16.
- 03:05 demo/variants/phone.html: iMessage-style simulator on the same conversation code as Photon. Tested with replays: help -> dispatch -> ack -> briefing -> floorplan + aerial -> follow-up answer.
