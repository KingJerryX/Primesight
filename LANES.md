# Primesight: the two lanes

How the two of us split the work for HackPrinceton (Nov 13-15) without stepping on each other. Each lane is one person. Read the part that applies to you, plus the shared rules at the end.

The live task board, with dropdowns to mark progress, is the shared doc "Primesight demo plan and task board". This file explains what each lane is for and how to do its tasks. The board tracks who is doing what.

---

## The product in one paragraph

A 911 call comes in, for example "cardiac arrest, 36th floor, 1000 Louisiana St, Houston". Primesight works out which building and floor that is and pulls three data sources:

- **CoStar:** who is inside, floor by floor, plus construction, sprinklers, elevators and the property manager.
- **Matterport:** 3D scans of the interior.
- **Vexcel:** aerial photos of the outside, plus city hydrant data.

It writes a one-screen briefing and texts it to the fire or EMS crew before they arrive. A Claude agent (built with LangChain's deepagents) does the reading and writing. Photon delivers it by text.

## Why two lanes, and how they're cut

We split by **what the briefing covers**, not by technology:

- **Lane A: Inside and the agent.** Everything about the interior of the building, and the AI brain that writes the briefing.
- **Lane B: Outside and delivery.** Everything about the outside of the building, and getting the briefing onto the phone and the projector.

Both lanes write Python, both build things people see in the demo, and each has about 21 hours of Must and Should work. Every file has exactly one owner, so we never edit the same file and never get merge conflicts.

```
              dispatch console (B)
                     │  POST /brief
                     ▼
   ┌──────────── server.py (A) ─────────────┐
   │  agent.py (A)  ──►  briefing.py (A)    │
   │                      │         │       │
   │        matterport.py (A)   outside.py (B)
   │        costar.py (A)       (aerial, hydrants,
   │        vision.py (A)        staging, markup)
   └──────────────┬─────────────────────────┘
                  │  briefing text + images
                  ▼
        messaging/ (B) ── Photon ──► the phone
                  │
        demo/ projector page (B) ◄── GET /trace (A)
```

## Where the two lanes meet

There are two points of contact. Agree on them now, and don't change them without telling the other person.

**1. The local server (Lane A builds it, Lane B calls it).**

```
POST /brief      {"text": "<the 911 call>", "thread": "<crew thread id>"}
             ->  {"briefing": "<sms text>", "images": ["<file path>", ...], "image_urls": ["/img/..."],
                  "property_id": 233899, "run_id": "<id>",
                  "texts": [{"to": "property manager", "text": "..."}],      # for building staff: show, never auto-send
                  "tenant_flags": [{"tenant", "floor", "risk", "why", "from_name_only"}]}

POST /ask        {"run_id": "<id from /brief>", "text": "<crew reply>"}
             ->  {"answer": "<1-3 line sms>"}   # continues that run's agent thread; reports get saved as building notes

POST /feedback   {"property_id": 233899, "text": "<crew reply>"}
             ->  {"ok": true}

GET  /trace/<run_id>
             ->  one JSON line per agent step:
                 {"t": <seconds>, "step": "find_building", "detail": "..."}
```

Lane B doesn't wait for Lane A. On day one, Lane B writes a tiny fake server that returns a fixed briefing in exactly this shape, and builds against it. When Lane A's real server is up, Lane B points at it instead.

**2. `outside.py` functions (Lane B owns them, Lane A's `briefing.py` calls them).** Keep these names and return shapes stable. You can add new functions freely.

| Function | Returns |
| --- | --- |
| `hydrants(lat, lon, city, radius_m=150)` | `[(metres, compass_dir, lat, lon), ...]`, nearest first. Empty if we have no hydrant layer for that city. |
| `roof(lat, lon)` | dict of Vexcel roof facts, for example `roof-characteristics.material.class` and `roof-objects.ac-unit.count` |
| `staging(lat, lon, text=..., radius_m=400, n=3)` | `[(score, metres, compass_dir, lat, lon), ...]` open lots found by Vexcel's image search |
| `ortho(lat, lon, path, half_m=75)` | saves an aerial JPEG of a square 150 m across and returns the path |
| `oblique(lat, lon, path, side="south", half_m=60)` | saves an angled aerial JPEG and returns the path |

---

## Lane A: Inside and the agent

### Your job

Make the briefing smart about the inside of the building, make the agent write it well, and serve it to Lane B. If the crew learns something useful about the building's interior, it came from your lane.

### You own

- `primesight/agent.py`: the deepagents orchestrator. It reads the call, finds the building, calls the tools and writes the final text.
- `primesight/briefing.py`: gathers the three data layers in parallel and writes the draft briefing, with fixed rules for each incident type (fire, medical, hazmat).
- `primesight/matterport.py`: finds the building's scans, picks the best one for the floor, and gets the floorplan and the scan points.
- `primesight/costar.py`: reads the local CoStar kit and gets the building's facts, tenants by floor and contacts.
- New: `primesight/vision.py` (SAM 3 over the scan photos) and `primesight/server.py` (the local server).
- `probes/`: the scripts we used to test the APIs.

### What already works

- **The draft briefing:** `python -m primesight.briefing 233899 30 fire` prints a full briefing in about 4 seconds. 233899 is the demo building (Wells Fargo Plaza, Houston, 71 floors).
- **The CoStar loader:**
  - It decodes the kit's floor codes into real floor numbers. Code 29 means 5th floor, not floor 29, and that's handled.
  - It caches each building in `cache/`.
- **The Matterport code:**
  - It reads the floor from each scan's name and picks the exact floor, or the nearest scanned floor if that one isn't scanned.
  - It downloads the floorplan and builds a graph of the scan points that you can walk.
- **The agent (`agent.py`, deepagents):** runs end to end on Opus 5.5 (cost and time below, under Grader).
  - **Tools:** `find_building`, `gather_building`, `add_building_note` (append-only, dated).
  - **Files:** one virtual file system. `/buildings/` (crew notes on disk), `/scans/` (SAM 3 results on disk) and `/playbook/` (our rules, `primesight/agentfs/`) are read-only to the agent; everything else is per-run scratch.
  - **Memory:** `primesight/agentfs/AGENTS.md`, always loaded: SMS format, facts not orders, notes outrank data, how to handle crew replies.
  - **Skills:** `primesight/agentfs/skills/{fire,medical,hazmat}/SKILL.md`. The agent reads only the one for this call.
  - **Subagents, run in parallel:** `tenant-risk` returns structured flags (`tenant_flags`); `notify-writer` queues texts for the building engineer and property manager (`texts`).
  - **Threads:** each run_id is a checkpointed thread, so `/ask` answers crew follow-ups in about 3 s for $0.05.
  - **Grader (RubricMiddleware):** every briefing is checked against a rubric (12 lines, facts only from tools, NOT KNOWN line, no orders, risks marked as guesses) and every crew answer against a shorter one. On a fail the agent revises; the verdict shows as `graded` in the trace. With the grader a run is about 50-60 s and $0.42-0.48.
  - **Trace:** subagent steps appear as `helper:<tool>` lines.

### Your tasks

Do them roughly in this order. Size: S is under 2 hours, M is 2 to 5, L is more.

1. **Run the agent end to end (Must, S).**
   - **What:** add the API key to `.env`, then run `python -m primesight.agent "Cardiac arrest, 36th floor, 1000 Louisiana St, Houston"`.
   - **How:** tune the system prompt in `agent.py` until the briefing is 12 lines or fewer and the most urgent line comes first.
   - **Done when:** the floor 36 medical call and the floor 30 fire call both come out right.
2. **Local server (Must, M).**
   - **What:** build `server.py` with the three endpoints in the contract above.
   - **How:** the standard library's `http.server` is enough; no framework needed.
   - **Done when:** `/brief` calls `agent.run`, returns the briefing and the image paths, and Lane B's console can reach it.
3. **Agent steps to `/trace` (Must, S).**
   - **What:** `agent.run` already prints each tool call. Write those steps to the run's trace as JSON lines too.
   - **Done when:** Lane B's projector page can show the steps while the agent is still running.
4. **Crew feedback into the notes file (Must, S).**
   - **What:** `/feedback` adds a dated line to `cache/buildings/<id>.md`, for example "Louisiana St doors locked, used Smith St". The agent already reads that file first.
   - **Done when:** the next briefing for that building leads with the note.
5. **Cache both demo calls (Must, S).**
   - **What:** the night before, run floor 36 medical and floor 30 fire so every API result sits in `cache/`.
   - **Done when:** the live demo never waits on a slow API.
6. **Find exits in the scans (Should, L).**
   - **What:** build `vision.py`, which runs SAM 3 over the scan photos of floors 36 and 37 to find exit signs and doors. It saves each find with its scan point and position to `cache/`.
   - **Already known:**
     - SAM 3 finds exit signs (confidence 0.76 to 0.88) and doors reliably.
     - It doesn't find sprinkler heads or fire extinguishers.
     - It takes about 0.36 s per photo per label, so a full floor takes minutes. Always run it ahead of time, never live.
   - **How to run:** use the `sam3_env` conda env (`HF_HUB_OFFLINE=1 ~/miniconda3/envs/sam3_env/python.exe`). `probes/sam3_test.py` is a working starting point.
   - **Watch out:** photo links from Matterport expire, so fetch them right before you use them.
7. **Mark up the floorplan (Should, M).**
   - **What:** draw the exits found in task 6, plus the target suite, onto the floorplan image with Pillow.
   - **Done when:** the marked-up floorplan is the image the phone receives.
8. **Rules for floors with no scan (Should, S).** In `briefing.py`:
   - If the scan is from another floor, only stairs, elevators and exits carry over, and the briefing names that floor ("Scan from floor 36: stairs and elevators line up, rooms may differ").
   - If the building has no scan, say "No interior scan" plainly.
   - Never carry over room names or a walking route from another floor.
9. **Test the tenant check (Should, S).**
   - **What:** pick a building from the kit with a clinic, school or lab tenant, run it, and confirm the helper flags it without inventing anything.
10. **Walking route (Stretch, M).**
    - **What:** the shortest path over the scan-point graph (`matterport.walk_graph`) from the scan's entrance to the suite, turned into one plain line ("from the elevator lobby, left, about 30 m").

### Your demo moments

- The briefing's wording on the phone.
- The marked-up floorplan.
- The floor 30 run, which proves we're honest about floors with no scan.

---

## Lane B: Outside and delivery

### Your job

Make the outside of the building visible, and get every briefing onto the one demo phone and the projector. If the judges see something move, it came from your lane.

### You own

- `primesight/outside.py`: Vexcel aerial images, roof facts, staging-lot search, city hydrant data, and (new) drawing on the aerial image.
- New: `messaging/`, the Photon 2.0 code (TypeScript). It sends to the phone and receives the crew's replies.
- New: `demo/`, two small web pages: the dispatch console and the projector page.

### What already works

- `outside.py`:
  - **Hydrants:** finds hydrants from Houston's public city data. For the demo building it finds 15 within 150 m, the nearest 61 m north.
  - **Roof:** gets the roof material and roof equipment from Vexcel.
  - **Staging:** finds open lots nearby with Vexcel's image search.
  - **Aerial images:** saves aerial photos. Vexcel calls take about 0.3 to 1.4 s each.
  - Run `python -m primesight.outside` to see it all.
- Nothing in `messaging/` or `demo/` exists yet.

### Your tasks

1. **Photon 2.0 hello world (Must, S).**
   - **What:** sign up for the Photon 2.0 beta (free), create a project and set up a number.
   - **Done when:** a text from our phone gets an automatic reply from your code.
   - **Guidelines:** https://somethingreat.notion.site/Hackathon-Guidelines-for-Photon-2-0-3f8e443f36bb81f9893fed0d08a4c01d
2. **Settle the voice question (Must, S).**
   - **What:** find out whether Photon 2.0 can take an incoming phone call and give us its text, and whether it can place a call. Ask the Photon mentors if the docs don't say.
   - **Done when:** the answer is written in the task board doc. This decides whether task 9 happens.
3. **Dispatch console page (Must, M).**
   - **What:** `demo/console.html`, a page on the laptop where the dispatcher picks one of the demo calls (or types one) and presses Send. It posts to `/brief`.
   - **How:** build it against your fake server first.
   - **Done when:** pressing Send starts a run. Since we only have one phone, this page replaces a dispatcher's phone.
4. **Briefing out to the phone (Must, M).**
   - **What:** when `/brief` returns, Photon sends the briefing text and both images (floorplan and aerial) into the phone's thread.
   - **How:** test over iMessage and over plain SMS. Plain SMS needs plain ASCII text, which the briefing already uses.
   - **Done when:** the phone buzzes with the full briefing during the demo.
5. **Mark up the aerial image (Must, M).**
   - **What:** in `outside.py`, draw the hydrants, the staging lot and the building outline onto the Vexcel aerial image with Pillow.
   - **How:** the image covers the square from `square(lat, lon, half_m)`, so a point's pixel position is roughly `x = (lon - west) / (east - west) * width` and `y = (north - lat) / (north - south) * height`. Check it on one hydrant against the photo.
   - **Done when:** `briefing.py` can send the marked-up file instead of the plain one.
6. **Projector page (Must, M).**
   - **What:** `demo/projector.html` shows `/trace` while it runs: each step, the helper agent, the notes file being written. Next to it, it shows the texts Primesight sent to people other than the crew (building engineer, property manager).
   - **Done when:** it's the second screen in the demo, and readable from the back of the room.
7. **Crew replies to `/ask` (Should, S).**
   - **What:** when the judge replies on the phone ("which elevator fits a stretcher?" or "Louisiana St doors locked"), Photon passes it to `/ask` with the run_id and texts the answer back. The agent saves reports as building notes itself. `/feedback` still adds a note directly, without the agent.
   - **Done when:** that reply shows up in the next briefing for the building.
8. **Texts to other people (Should, S).**
   - **What:** Primesight writes "Engine 1 arriving at the freight elevator in 4 min, please meet them" for the building engineer, and a similar text for the property manager.
   - **Lane A side is done:** the `notify-writer` subagent returns them in `/brief`'s `texts` (only for contacts on file; none means none on record).
   - **How:** with one phone, these show on the projector. If the second phone can still receive texts, send them there for real. The kit's phone numbers are fake (555), so never send to them.
9. **Hands-free follow-up call (Stretch, M).**
   - **What:** if task 2 says yes, the crew calls Primesight from the phone and asks "which elevator?" out loud, and hears the answer from the same facts.
10. **Second demo building (Stretch, M).**
    - **What:** a low-rise industrial building in Houston from the kit, where the aerial view shows loading docks and the roof. Aerial detection works much better on low buildings than on downtown towers.

### Photon 2.0: what we know (from our HackHarvard build and the beta API spec)

We shipped a Photon 2.0 iMessage bot at HackHarvard (`apps/orchestrator/src/messenger.ts` and `voice.ts` there). Copy those patterns. SDK: `@photon-ai/api` 0.5.x. Spec: https://api.photon.codes/openapi.json.

- **Setup.**
  - `new Photon({ headers: { Authorization: `Bearer ${PHOTON_API_KEY}` } })`.
  - The key needs project:read and project:write (and platforms:read/write for voice).
  - Copy the project id (`pho_prj_...`) from the dashboard at app.beta.photon.codes. A `pho_sk_` key can't list projects (403).
  - Env: `PHOTON_API_KEY`, `PHOTON_PROJECT_ID`, `PHOTON_PLATFORM=imessage`. Keys go in `.env` only.
- **Shared iMessage lines (the free beta setup).**
  - Register the demo phone first: `projects.platforms.imessage.assignments.create({ path, body: { handle }, headers: { idempotencyKey } })`. This returns the line's number. Up to 25 phones.
  - The line can't text first. The phone must text the line once after it's registered (we used "START"). Do this before the demo starts.
  - Sending works only in direct (1:1) threads. No group threads.
  - Reply into the thread using the `spaceId` and `senderId` from the phone's inbound message. Don't build them yourself.
  - Don't set `parentMessageId`; shared lines reject it.
- **Receiving.**
  - Poll `projects.listMessages({ path, query: { createdSince, direction: "received", order: "asc", pageSize: 100, platform } })` every 2 s, follow `pageToken`, and dedupe by message id.
  - Crew replies go to `POST /ask` (keep each thread's last run_id).
- **Sending text.**
  - `projects.sendMessage({ path: { id }, headers: { "idempotency-key": uuid }, body: { spaceId, senderId, content: { type: "text", format: "plain", text } } })`.
- **Sending images (floorplan, aerial).** Two calls per image:
  1. `POST /v1/projects/{id}/attachments` with a `multipart/related` body (not form-data) in exactly two parts:
     - JSON `{ contentType: "image/jpeg", sizeBytes, filename }`. `sizeBytes` must equal the file length.
     - The file bytes.
     - Headers: `content-length`, `content-type` (with the boundary) and `idempotency-key`.
     - It returns `{ id }`.
  2. `sendMessage` with `content: { type: "attachment", attachmentId: id, metadata: { caption: { format: "plain", text } } }`.
  - Not live-tested yet. Images come from `GET /img/...` on our server (`image_urls` in the `/brief` reply).
- **Voice (task 2, settled on paper).** Photon voice is a phone number plus a SIP trunk. Photon doesn't do speech itself; a voice agent does (we used ElevenLabs).
  - **Outgoing calls:** `POST https://api.elevenlabs.io/v1/convai/sip-trunk/outbound-call` with `agent_id`, `agent_phone_number_id`, `to_number` and `conversation_initiation_client_data.dynamic_variables`. Put the briefing facts in `dynamic_variables`.
  - **Incoming calls:** set the voice profile's inbound destination (`PUT .../platforms/voice/profiles/{profileId}/inbound` with a SIP `destinationUri`) to the voice agent.
  - Needs an SMS number with a voice profile. A number costs money: check the price with the team before buying.
  - Neither direction is tested on a real number yet. Task 9 stays a stretch goal.

### Your demo moments

- The phone buzzing with the briefing.
- The marked-up aerial image.
- The live step list on the projector.
- The judge's reply changing the next briefing.

---

## Shared rules

**Order of work over the weekend:**

- **Friday night:**
  - Lane A: agent running end to end, then the server.
  - Lane B: Photon hello world, the voice question, then the console against the fake server.
- **Saturday:**
  - Lane A: vision and the floorplan.
  - Lane B: briefing to the phone, the aerial markup, the projector page.
- **Saturday evening:** connect the two lanes, cache both demo calls, and record the backup video.
- **Sunday:** two timed rehearsals, then present.

**The demo setup:** one phone, held by a judge as the crew. The dispatcher sends the call from the console on the laptop. The narrator drives the projector. The main run is a medical call on floor 36, which has its own scan. The second run is a fire on floor 30, which has no scan, so it shows the fallback.

**Git:**

- **Branches:** work on your own branch (`inside` or `outside`) and merge to `main` with a pull request.
- **Shared files:** only `README.md` and `.gitignore`. Tell the other person before you edit them.
- **The repo is public, so never commit any of these:**
  - `.env` (API keys)
  - `costar-hackathon-kit/`
  - `cache/`
  - `output/`
  - the Vexcel tutorial materials
- **Keys:** get them from your teammate directly, not through the repo.

**Setup:**

- **Python:** 3.12 or newer. Install the packages with `pip install deepagents langchain-anthropic shapely pillow requests python-dotenv networkx`.
- **CoStar kit:** download it yourself as a participant and put it in `costar-hackathon-kit/`.
- **Keys:** put them in `.env`: `VEXCEL_API_KEY`, `MATTERPORT_TOKEN_ID`, `MATTERPORT_TOKEN_SECRET`, `MATTERPORT_SDK_KEY`, `ANTHROPIC_API_KEY`.
- **Photon (Lane B):** Node.js for the TypeScript code.
- **Vision (Lane A):** a CUDA GPU for SAM 3. The SAM 3 env lives on one laptop, so that work stays with Lane A.

**When something breaks:** the briefing must still go out. Every data source fails soft into the "NOT KNOWN" line, so keep it that way in your own code.
