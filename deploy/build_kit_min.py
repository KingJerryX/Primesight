"""kit_min/: the two CoStar kit files the deployed server reads, cut to the buildings we preprocessed.

The raw kit stays local and out of git. The deploy needs only the address index (find_building), restricted to buildings
with a cached record in cache/costar_<id>.json, plus the enum table. kit_min/ is gitignored; `vercel deploy` uploads it.
  python deploy/build_kit_min.py
"""
import csv, glob, os, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT, OUT = os.path.join(ROOT, "costar-hackathon-kit"), os.path.join(ROOT, "kit_min")
ids = {n[7:-5] for n in map(os.path.basename, glob.glob(os.path.join(ROOT, "cache", "costar_*.json"))) if n[7:-5].isdigit()}
os.makedirs(os.path.join(OUT, "schemas"), exist_ok=True)
with open(os.path.join(KIT, "commercial-properties.csv"), encoding="utf-8") as fh, \
        open(os.path.join(OUT, "commercial-properties.csv"), "w", encoding="utf-8", newline="") as out:
    r = csv.DictReader(fh)
    w = csv.DictWriter(out, r.fieldnames)
    w.writeheader()
    rows = [row for row in r if row["PropertyId"] in ids]
    w.writerows(rows)
shutil.copy(os.path.join(KIT, "schemas", "enums.json"), os.path.join(OUT, "schemas", "enums.json"))
assert rows and {row["PropertyId"] for row in rows} <= ids
print(f"kit_min: {len(rows)} address rows for {len(ids)} cached buildings, enums copied")
