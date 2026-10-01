import sys
sys.path.insert(0, "app")
from campaign.pack import load_pack, PackError

try:
    pack = load_pack("campaigns/ashiorid_office")
    print("scenes:", len(pack.scenes), "| lore:", sorted(pack.lore.keys()))
    print("ambient:", pack.ambient_scene_ids())
    print("cast:", sorted(pack.cast.keys()))
except PackError as e:
    print("PackError:", e)
