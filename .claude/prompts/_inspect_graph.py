import yaml, pathlib, sys

SC = pathlib.Path("/home/secus/codeProjects/virtualTubers/campaigns/ashiorid_1/scenes")
inbound = {}
for p in sorted(SC.glob("*.yaml")):
    d = yaml.safe_load(p.read_text())
    if d.get("ambient"):
        continue
    sid = d["id"]
    for b in d.get("branches") or []:
        inbound.setdefault(b["next"], []).append(f"{sid}[{b['when'].get('outcome')}]")
    dn = d.get("default_next")
    if dn:
        inbound.setdefault(dn, []).append(f"{sid}[default]")

print("=== TRUE predecessors ===")
for k in ["magic-retained", "magic-lost", "letos-manor", "grovley-revelation",
          "burn-it-down", "malmont-arrival", "bahadur-revealed"]:
    print(f"  {k:22s} <- {inbound.get(k)}")

print("\n=== CONVERGENCE POINTS (>1 inbound path) ===")
for k, v in sorted(inbound.items()):
    if len(v) > 1:
        print(f"  {k:22s} <- {v}")
