# Texting Primesight from your own phone (Photon 2.0)

About 10 minutes. You type the key and your number yourself; neither ever goes in chat, a commit or a URL.

## 1. Photon project (dashboard, about 3 min)
1. Sign in to Photon 2.0 (link in the hackathon Notion guidelines) and create a project.
2. Turn on **iMessage** with the **shared line** (free, works right away).
3. Create a **project API key**. Copy it, and also copy the project id (`pho_prj_...`).

## 2. Keys into `.env` (project root, gitignored)
```
PHOTON_API_KEY=<paste>
PHOTON_PROJECT_ID=pho_prj_...
```
Optional: `PHOTON_PLATFORM=imessage` limits polling to iMessage. Leave it unset at first; the platform filter has hidden texts before.

## 3. Register your phone with the shared line
In a terminal, with your own number typed by you:
```bash
python -m primesight.photon_bridge register "+1YOURNUMBER"
```
It prints `Text START to <shared line> from that phone.` The shared line cannot text first, so do that now: send **START** from your phone. Primesight answers with the how-to.

## 4. Run it
```bash
python -m primesight.server
```
The console should say `Photon bridge: polling`, and http://localhost:8787/health should show `"photon": true`.
Open a projector on the laptop, for example http://localhost:8787/projector-graph.html.

## 5. Try it from the phone
| Text | What you should get |
|---|---|
| `Smoke on the 30th floor, 1000 Louisiana St, Houston` | "got it" within ~3 s, typing dots, then the briefing (~30-60 s live), then floorplan and aerial images |
| `Which elevator fits a stretcher?` | answered from the same building and run (the agent remembers the call) |
| `NOTE stairwell B door sticks` | saved; the next briefing for that building leads with it |
| `Cardiac arrest on the 36th floor, 1000 Louisiana St, Houston. Caller is a coworker, patient is unresponsive.` | recorded run; instant with `PRIMESIGHT_REPLAY=1` |

Every text you send is mirrored on `phone.html?id=ph-<hash>`, and every run shows on the projectors. Live runs cost about $0.30-0.50 each. Replays are free.

## If something goes wrong
- **No reply at all**: did you text START first? Is the console printing `[photon] poll failed`? A 401 or 403 means the key or project id is wrong (Photon answers a bad key with 403).
- **Reply arrives but no images**: Photon rejected the upload. The bridge falls back to a text link, which only opens if `PRIMESIGHT_PUBLIC_URL` points to a reachable address.
- **Group chats** are ignored on purpose (a briefing goes to one crew).
- **Fallback with no Photon**: `PRIMESIGHT_HOST=0.0.0.0 python -m primesight.server`, then open `http://<laptop Wi-Fi IP>:8787/phone.html` on the phone. It is the same conversation code as the Photon line. Windows may ask to let Python through the firewall; that is your call.

## What the bridge does (primesight/photon_bridge.py)
- Polls received messages every 2 s (`createdSince`, ascending, follows `nextPageToken`, dedupes by id, moves the window only after paging finishes).
- Replies with `spaceId = message.space.id` and `senderId = message.receivedBy.id`, plus an idempotency key on every send.
- Shows the typing indicator while the agent works. Images go up as `multipart/related` attachments.
- Conversations are keyed by a hash of the phone handle, so numbers never appear in logs, traces or page URLs.
- `python -m primesight.photon_bridge selftest` runs the whole round trip against a local mock (no network).
