# DRAFT (P2.5), awaiting user review
"""
campaigns/ashiorid/profiles/validate_profiles.py

Validate the D&D (ashiorid) character profiles against campaigns/ashiorid/profiles/_SCHEMA.md,
modelled on scripts/validate_office_profiles.py, and check the BELIEVED/TRUTH INVARIANT:

    For each player, nothing player-visible (backstory.believed, the lore text of every
    `knowledge` stem, backstory_nodes, objectives, wants/fears, speech, relationships,
    personality, identity, appearance) contains a truth-only proper noun or key phrase.

Truth-only terms for a player =
    (a) proper nouns found mid-sentence in that player's `truth`, the GM `truth`, and every lore
        note that is NOT one of the player's start knowledge stems,
        minus anything present in the START CORPUS (that player's cast file, the start lore
        stems' text, scene `invitation`, the campaign title);
    (b) a curated list of key phrases (KEY_PHRASES) that give the twist away.

Run:  .venv/bin/python campaigns/ashiorid/profiles/validate_profiles.py
Exit: 0 when no errors (warnings allowed), 1 otherwise.
"""
import pathlib
import re
import sys

import yaml

HERE = pathlib.Path(__file__).resolve().parent
PACK = HERE.parent
PLAYERS = ["chadwick", "Leena", "Vigil", "sodacan_bob"]
SEATS = {"gm": ("tuber_0", 0), "chadwick": ("tuber_1", 1), "Leena": ("tuber_2", 2),
         "Vigil": ("tuber_3", 3), "sodacan_bob": ("tuber_4", 4)}
GM_BLOCKS = ["style", "table_rules", "secrets", "unlocks"]

NODE_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+){1,7}$")
VERBS = frozenset("knows lives wants fears trusts likes dislikes believes remembers is has can "
                  "cannot owes suspects hopes".split())
LOOP_SENTENCE = "Never state or imply that time repeats."
BANNED_RE = re.compile(r"\b(simulation|simulated|time loop|looping|AI|artificial intelligence|"
                       r"stream|script|NPC)\b", re.IGNORECASE)

# Key phrases that give the twist away even without a proper noun. Lower-case, word-bounded.
KEY_PHRASES = [
    "begene", "breeding", "bloodline", "bloodlines", "grovley", "bahadur", "muad'malik",
    "eighty-sixth", "86th", "signet", "father to all", "half-sibling", "half-siblings",
    "half-brother", "half-sister", "siblings", "placement", "placed with", "assembled",
    "ledger", "subbasement", "the vault", "the worm", "malmont", "jasper", "juniper", "amulet",
    "ruby", "rubies", "malvakar", "pocket universe", "pocket-universe", "four cribs", "cribs",
    "taken at birth", "born leena", "not chance", "elf-slavers", "slavers", "immortal",
    "harvest", "seal holds", "seal fails", "leto's son", "leto's daughter", "my father was a duke",
]
# Case-sensitive tokens that need exact case (short words that are common in lower case).
KEY_TOKENS_CASED = ["Ord"]

STOP = set("""A An The This That These Those It Its He She They His Her Their We You I If When
Then After Before Every Everything Everyone Nobody Nothing Some Most Both One Two Three Four
Ten GM ONLY Spine Branch NOT Optional Placements Their Duke Event Age War""".split())


def load(p):
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def flatten(obj):
    """All string leaves of a nested structure, joined."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return "\n".join(flatten(v) for v in obj.values())
    if isinstance(obj, list):
        return "\n".join(flatten(v) for v in obj)
    return "" if obj is None else str(obj)


def proper_nouns(text):
    """Capitalised tokens (incl. Muad'Malik, multiword joined later) that are NOT sentence-initial."""
    out = set()
    for m in re.finditer(r"(?<![.!?:\n\"(]\s)(?<!^)\b([A-Z][a-zA-Z']+(?:-[A-Za-z]+)?)", text):
        start = m.start(1)
        prev = text[:start].rstrip()
        if not prev or prev[-1] in ".!?:\n\"(-":
            continue
        tok = re.sub(r"'s?$", "", m.group(1))
        if tok and tok not in STOP and len(tok) > 1:
            out.add(tok)
    return out


def lore_text(stem):
    p = PACK / "lore" / f"{stem}.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def check_structure(cid, prof, errors, warnings):
    for key in ["id", "retains_fragments", "is_main", "table_role", "seat", "turn_order_pos",
                "wants", "fears", "speech", "relationships", "knowledge", "identity",
                "appearance", "personality", "objectives", "backstory", "backstory_nodes",
                "behaviour_contract"]:
        if key not in prof:
            errors.append(f"{cid}: missing key '{key}'")
    if prof.get("id") != cid:
        errors.append(f"{cid}: id is {prof.get('id')!r}")
    seat, pos = SEATS[cid]
    if prof.get("seat") != seat or (prof.get("identity") or {}).get("seat") != seat:
        errors.append(f"{cid}: seat must be {seat}")
    if prof.get("turn_order_pos") != pos:
        errors.append(f"{cid}: turn_order_pos must be {pos}")
    if not (PACK / "cast" / f"{cid}.yaml").exists():
        errors.append(f"{cid}: no cast/{cid}.yaml")
    for k, lo, hi in [("wants", 2, 4), ("fears", 2, 4)]:
        n = len(prof.get(k) or [])
        if not lo <= n <= hi:
            errors.append(f"{cid}: {k} has {n} items (want {lo}-{hi})")
    rel = prof.get("relationships") or {}
    others = [c for c in SEATS if c not in (cid, "gm")]
    for o in others:
        if o not in rel:
            errors.append(f"{cid}: relationships missing '{o}'")
    for k in rel:
        if k not in SEATS:
            errors.append(f"{cid}: relationships key '{k}' is not a cast id")
    for stem in prof.get("knowledge") or []:
        if lore_text(stem) is None:
            errors.append(f"{cid}: knowledge stem '{stem}' has no lore/{stem}.md")
    nodes = prof.get("backstory_nodes") or []
    if not 8 <= len(nodes) <= 20:
        errors.append(f"{cid}: {len(nodes)} backstory_nodes (want 8-20)")
    seen = set()
    for n in nodes:
        name = n.get("name", "")
        if not NODE_NAME_RE.match(name) or name.split("-")[0] not in VERBS:
            errors.append(f"{cid}: bad node name '{name}'")
        if name in seen:
            errors.append(f"{cid}: duplicate node '{name}'")
        seen.add(name)
    if LOOP_SENTENCE not in (prof.get("behaviour_contract") or []):
        errors.append(f"{cid}: behaviour_contract lacks the loop sentence")
    bs = prof.get("backstory") or {}
    if not (bs.get("believed") or "").strip():
        errors.append(f"{cid}: empty believed")
    if not (bs.get("truth") or "").strip():
        errors.append(f"{cid}: empty truth")
    wc = len((bs.get("believed") or "").split())
    if cid != "gm" and not 600 <= wc <= 1200:
        warnings.append(f"{cid}: believed is {wc} words (office target 600-1200)")
    hits = sorted({m.group(0) for m in BANNED_RE.finditer(bs.get("believed") or "")})
    if hits:
        errors.append(f"{cid}: banned meta words in believed: {hits}")
    ident = prof.get("identity") or {}
    for k in ["age", "home"]:
        if ident.get(k) is None and cid != "gm":
            warnings.append(f"{cid}: identity.{k} UNFILLED (not in sources)")
    if cid == "gm":
        for b in GM_BLOCKS:
            if b not in prof:
                errors.append(f"gm: missing GM-only block '{b}'")
    else:
        for b in GM_BLOCKS:
            if b in prof:
                errors.append(f"{cid}: GM-only block '{b}' present in a player profile")


def check_invariant(cid, prof, gm, campaign):
    """Return (truth_only_terms, hits) for one player."""
    cast = load(PACK / "cast" / f"{cid}.yaml")
    start_stems = prof.get("knowledge") or []
    scene01_doc = load(PACK / "scenes" / "01-invitation.yaml")
    scene01 = (PACK / "scenes" / "01-invitation.yaml").read_text(encoding="utf-8")
    # Lore that is legitimately known at campaign start = the `lore:` list of the opening scene.
    allowed_stems = list(scene01_doc.get("lore") or [])
    # The four meet at the manor in the first session, so every cast member's NAME is start
    # knowledge (their homes/backstories are not).
    party_names = [str(load(p).get("name", "")) for p in (PACK / "cast").glob("*.yaml")]
    common = "\n".join([scene01, str(campaign.get("title", ""))] + party_names +
                        [lore_text(s) or "" for s in allowed_stems])
    # Proper nouns: the character's own cast file is start knowledge (e.g. Nafsari for Vigil).
    start_lower = (common + "\n" + flatten(cast)).lower()
    # Key phrases: the cast file is NOT exempt, so a twist phrase that the cast prompt already
    # leaks (see the cast-leak note) is still refused in the profile.
    common_lower = common.lower()

    secret_text = "\n".join([prof["backstory"]["truth"], gm["backstory"]["truth"]] +
                            [p.read_text(encoding="utf-8") for p in (PACK / "lore").glob("*.md")
                             if p.stem not in allowed_stems])
    nouns = {t for t in proper_nouns(secret_text)
             if not re.search(r"\b" + re.escape(t.lower()) + r"\b", start_lower)}
    phrases = [p for p in KEY_PHRASES if p not in common_lower]
    extra = [s for s in start_stems if s not in allowed_stems]

    visible = {k: v for k, v in prof.items() if k != "backstory"}
    visible_text = flatten(visible) + "\n" + prof["backstory"]["believed"]
    knowledge_text = "\n".join(lore_text(s) or "" for s in start_stems)

    hits = []
    for where, text in [("profile", visible_text), ("knowledge-lore", knowledge_text)]:
        low = text.lower()
        for t in sorted(nouns) + KEY_TOKENS_CASED:
            if re.search(r"\b" + re.escape(t) + r"\b", text):
                hits.append((where, t))
        for p in phrases:
            if re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", low):
                hits.append((where, p))
    # pre-existing cast-file leaks (outside this folder; info only)
    cast_leaks = [p for p in KEY_PHRASES
                  if re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])",
                               flatten(cast).lower())]
    hits += [("knowledge", f"stem '{s}' is not start lore {allowed_stems}") for s in extra]
    return nouns, phrases, sorted(set(hits)), cast_leaks


def main():
    errors, warnings = [], []
    campaign = load(PACK / "campaign.yaml")
    profiles = {}
    for cid in SEATS:
        p = HERE / f"{cid}.yaml"
        if not p.exists():
            errors.append(f"missing profiles/{cid}.yaml")
            continue
        first = p.read_text(encoding="utf-8").splitlines()[0]
        if first != "# DRAFT (P2.5), awaiting user review":
            errors.append(f"{cid}: missing DRAFT header")
        profiles[cid] = load(p)
        check_structure(cid, profiles[cid], errors, warnings)
    if campaign.get("players") != [c for c in SEATS if c != "gm"]:
        warnings.append("campaign.yaml players order differs from the seat order")

    print("== believed/truth invariant ==")
    ok = True
    for cid in PLAYERS:
        if cid not in profiles or "gm" not in profiles:
            continue
        nouns, phrases, hits, cast_leaks = check_invariant(cid, profiles[cid], profiles["gm"],
                                                           campaign)
        status = "PASS" if not hits else "FAIL"
        ok &= not hits
        print(f"{cid:12s} {status}  truth-only proper nouns checked={len(nouns)} "
              f"key phrases checked={len(phrases)} knowledge={profiles[cid]['knowledge']}")
        for where, t in hits:
            print(f"    LEAK in {where}: {t!r}")
            errors.append(f"{cid}: truth-only term {t!r} in {where}")
        if cast_leaks:
            print(f"    note: cast/{cid}.yaml (not in this folder) already contains: {cast_leaks}")
    print("truth-only proper nouns (sample, chadwick):",
          sorted(check_invariant("chadwick", profiles["chadwick"], profiles["gm"], campaign)[0])
          if "chadwick" in profiles and "gm" in profiles else "-")

    print("\n== structure ==")
    for w in warnings:
        print("WARN ", w)
    for e in errors:
        print("ERROR", e)
    print(f"\n{len(profiles)} profiles loaded; {len(errors)} errors, {len(warnings)} warnings; "
          f"invariant {'PASS' if ok else 'FAIL'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
