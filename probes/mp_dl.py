import os, requests
from mp import gql
OUT = os.path.join(os.path.dirname(__file__), "out"); os.makedirs(OUT, exist_ok=True)
MID = "Gj2E4uqqdfU"  # Level 53: a full floor, most likely to show core (elevators, stairs)

_, d = gql("""query($id:ID!){model(id:$id){
  assets{floorplans(provider:"matterport", flags:[photogramy]){format url}}
  locations{id neighbors position{x y z} panos{skybox(resolution:"2k"){children}}}
  geocoordinates{ geoLocationOf(modelLocation:{x:0,y:0,z:0}){lat long} }
}}""", {"id": MID})
m = d["data"]["model"]
print("geoLocationOf origin:", m["geocoordinates"])
fp = next(f for f in m["assets"]["floorplans"] if f["format"] == "jpg")
open(f"{OUT}/floorplan_{MID}.jpg", "wb").write(requests.get(fp["url"], timeout=60).content)
locs = m["locations"]
print("sweeps:", len(locs), "| neighbors of first:", len(locs[0]["neighbors"]))
# grab skybox faces from 3 spread-out sweeps
for i in (0, len(locs) // 2, len(locs) - 1):
    kids = locs[i]["panos"][0]["skybox"]["children"]
    for j in (1, 2, 3, 4):  # side faces (0 = up, 5 = down per skybox convention)
        open(f"{OUT}/sweep{i}_face{j}.jpg", "wb").write(requests.get(kids[j], timeout=60).content)
print("saved to", OUT, sorted(os.listdir(OUT)))
