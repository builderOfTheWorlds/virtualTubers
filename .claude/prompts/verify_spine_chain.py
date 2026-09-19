import sys
sys.path.insert(0, "app")
from campaign.pack import load_pack
p = load_pack("campaigns/ashiorid")
sc = p.scenes
for i in ("temple-of-malar", "lighthouse-encounter", "mutants-encounter"):
    s = sc.get(i)
    if s:
        ns = " ".join(b.narration for b in s.beats)
        print(i, "-> next:", s.default_next,