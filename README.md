# Primesight

Building briefings for emergency responders, written by an AI agent while the crew is still on the way.

When a 911 call comes in, Primesight texts the responding crew a one-screen briefing about the building they're heading to. The briefing covers:

- **Inside:** floor layout, exits and exit signs, the route to the right suite, from Matterport 3D scans.
- **Outside:** the approach, the roof, where to stage the truck, and nearby hydrants, from Vexcel aerial imagery and open city data.
- **Who's in it:** tenants by floor, building age and construction, sprinklers and elevators, from CoStar building data.

The briefing changes with the incident type. A fire and a cardiac arrest on the 30th floor get different briefings for the same building. Primesight supports dispatch; it does not replace it, and it says plainly what it doesn't know.

Built at HackPrinceton Fall 2026.

## How it works (planned)

- An orchestrator agent built on [deepagents](https://github.com/langchain-ai/deepagents) plans the briefing and sends work to subagents, one for each data source.
- Vision models, including [SAM 3](https://huggingface.co/facebook/sam3), read the scan panoramas to find exits, doors and signs.
- Briefings go out by text through Photon, and each building's file improves after every incident.

## Status

Early prototype. We've confirmed that the data sources and APIs work; the agent pipeline is not built yet.

## Setup

Requires Python 3.12 or newer.

```bash
pip install deepagents shapely pillow requests python-dotenv
```

Create a `.env` file in the project root. It is gitignored, so it never leaves your machine:

```
VEXCEL_API_KEY=
MATTERPORT_TOKEN_ID=
MATTERPORT_TOKEN_SECRET=
MATTERPORT_SDK_KEY=
```

Sponsor data and API keys are provided to HackPrinceton participants and are **not** included in this repository.
