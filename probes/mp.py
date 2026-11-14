import json, os, sys, requests
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
AUTH = (os.environ["MATTERPORT_TOKEN_ID"], os.environ["MATTERPORT_TOKEN_SECRET"])
URL = "https://api.matterport.com/api/models/graph"

def gql(q, v=None):
    r = requests.post(URL, json={"query": q, "variables": v or {}}, auth=AUTH, timeout=60)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text[:300]

if __name__ == "__main__":
    q = sys.argv[1]
    v = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    code, out = gql(q, v)
    print(code, json.dumps(out, indent=1)[:int(os.environ.get("N", 4000))])
