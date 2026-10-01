import json
import sys
import urllib.request

job = sys.argv[1] if len(sys.argv) > 1 else "job_20260928T234430_3174d1"
url = f"http://localhost:8001/jobs/{job}"
with urllib.request.urlopen(url, timeout=10) as r:
    j = json.load(r)
print("status:", j["status"])
print("progress:", j.get("progress"))
print("llm_progress:", j.get("llm_progress"))
if j.get("result"):
    print("result:", json.dumps(j["result"])[:800])
if j.get("error"):
    print("error:", j["error"][:400])
