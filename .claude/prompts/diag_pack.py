import sys
sys.path.insert(0, "app")
from campaign.pack import load_pack
p = load_pack("campaigns/ashiorid")
print("type(p):", type(p))
print("has .scenes attr:", hasattr(p, "scenes"))
print("type(p.scenes):", type(getattr(p, "scenes", None)))
try:
    n = p.scenes
    if isinstance(n, list):
        print("len(list):", len(n))
        print("first elem type:", type(n[0]))
        print("first elem repr:", repr(n[0])[:120])
    elif isinstance(n, dict):
        keys = list(n.keys())
        print("dict keys[:3]:", keys[:3])
        print("first val type:", type(n[keys[0]]))
except Exception as e:
    print("iter error:", e)
# Show what attributes p has
print("dir(p):", [a for a in dir(p) if not a.startswith("_")])
