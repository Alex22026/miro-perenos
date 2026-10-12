"""GitHub Actions: jobs/cleanup.json = {"mode":"list"} → results/all_boards.json;
{"mode":"delete","ids":[...]} → удаляет перечисленные доски, журнал в results/deleted.json"""
import os, json, time, urllib.request, urllib.error, urllib.parse
TOKEN = os.environ["MIRO_TOKEN"]; API = "https://api.miro.com/v2"
def call(method, path):
    for t in range(6):
        req = urllib.request.Request(API + path, method=method, headers={"Authorization": "Bearer " + TOKEN, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                txt = r.read().decode(); return r.status, (json.loads(txt) if txt else {})
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500: time.sleep(3 + 3 * t); continue
            return e.code, {"message": e.read().decode(errors="ignore")[:300]}
    return 0, {}
job = json.load(open("jobs/cleanup.json")); os.makedirs("results", exist_ok=True)
if job["mode"] == "list":
    out, off = [], 0
    while True:
        st, r = call("GET", f"/boards?limit=50&offset={off}")
        data = r.get("data") or []
        for b in data:
            out.append({"id": b["id"], "name": b.get("name"), "created": b.get("createdAt"),
                        "access": ((b.get("policy") or {}).get("sharingPolicy") or {}).get("access")})
        off += 50
        if not data or off >= r.get("total", 0): break
    json.dump(out, open("results/all_boards.json", "w"), ensure_ascii=False, indent=0); print(len(out))
else:
    res = {}
    for i in job["ids"]:
        st, r = call("DELETE", "/boards/" + urllib.parse.quote(i, safe=""))
        res[i] = st; print(i, st, r)
    json.dump(res, open("results/deleted.json", "w"), indent=0)
