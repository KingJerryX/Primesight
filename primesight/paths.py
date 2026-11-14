"""Where code reads and where it writes.

ROOT  the repo (read-only on Vercel)
DATA  where cache/ and output/ live and get written: ROOT on a laptop; on Vercel /tmp/primesight, seeded once per
      instance with a copy of the deployed cache/ and output/ (the only writable disk there is /tmp)
KIT   the CoStar kit: the full local download, else kit_min/ (the address index and enums only, built by
      deploy/build_kit_min.py; it ships with the Vercel deploy and is never committed)
"""
import os, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = ROOT
if os.environ.get("VERCEL"):
    DATA = "/tmp/primesight"
    if not os.path.exists(os.path.join(DATA, ".seeded")):
        for d in ("cache", "output"):
            shutil.copytree(os.path.join(ROOT, d), os.path.join(DATA, d), dirs_exist_ok=True)
        open(os.path.join(DATA, ".seeded"), "w").close()
KIT = next((p for p in (os.path.join(ROOT, "costar-hackathon-kit"), os.path.join(ROOT, "kit_min")) if os.path.isdir(p)),
           os.path.join(ROOT, "costar-hackathon-kit"))
