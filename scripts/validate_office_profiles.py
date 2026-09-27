"""
scripts/validate_office_profiles.py
Validate the ashiorid_office character files (OB-10c) against the contract in
campaigns/ashiorid_office/profiles/_SCHEMA.md.

For each of the 8 office roles it checks cast/<id>.yaml and profiles/<id>.yaml:
presence, required keys and types, seat / turn order / office_role agreement
with app/office/roles.py, voice registry + casting table, lore tenure, backstory
node names (regex + verb allowlist + uniqueness), knowledge ⊆ node names,
relationship keys, mandatory system_prompt sentences, banned meta words, and
word-count bounds (warnings).

Run:
    .venv/Scripts/python.exe scripts/validate_office_profiles.py
    .venv/Scripts/python.exe scripts/validate_office_profiles.py --pack campaigns/ashiorid_office --json

Exit code: 0 when there are no errors (warnings allowed), 1 otherwise.
See docs/validate_office_profiles.md.
"""
import argparse
import json
import logging
import pathlib
import re
import sys
import uuid

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO / "app") not in sys.path:
    sys.path.insert(0, str(REPO / "app"))

from office.roles import SEAT, OfficeRole  # noqa: E402

log = logging.getLogger("validate_office_profiles")
TRACE = 5
logging.addLevelName(TRACE, "TRACE")
RUN_ID = uuid.uuid4().hex[:8]


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, "run_id=%s " + msg, RUN_ID, *args)


# ---- contract constants (mirrors _SCHEMA.md; keep in sync) -----------------

IDS = [r.value for r in OfficeRole]

#: role -> voice registry key (_SCHEMA.md "Voices").
VOICE_FOR = {
    "ceo": "baritone_mid",
    "tech_lead": "bass_low",
    "analyst": "alto_warm",
    "engineer": "tenor_high",
    "tester": "tenor_low",
    "marketing": "baritone_soft",
    "office_manager": "alto_bright",
    "party_member": "narrator_plain",
}

#: role -> tenure in months (_SCHEMA.md "Canon fixed by lore").
TENURE_MONTHS = {
    "tech_lead": 96,
    "office_manager": 60,
    "analyst": 36,
    "engineer": 24,
    "tester": 18,
    "ceo": 14,
    "party_member": 13,
    "marketing": 10,
}

NODE_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+){1,7}$")
VERBS = frozenset(
    "knows lives wants fears trusts likes dislikes believes remembers is has can "
    "cannot owes suspects hopes".split())

LOOP_SENTENCE = "Never state or imply that time repeats."
OBSERVER_SENTENCE = "You never speak. You only observe."

BANNED_WORDS = ["simulation", "simulated", "time loop", "looping", "loop", "AI",
                "artificial intelligence", "stream", "script", "NPC"]
BANNED_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in BANNED_WORDS) + r")\b", re.IGNORECASE)

#: (field label, min words, max words) — warnings only.
WORD_BOUNDS = {
    "backstory.believed": (600, 1200),
    "appearance": (80, 200),
    "system_prompt": (150, 350),
}

# Type specs. A spec is a python type, a tuple of types, "list[str]",
# "list[node]", "dict[str]" or a nested dict spec.
STR, INT, BOOL = "str", "int", "bool"

CAST_SPEC = {
    "name": STR,
    "archetype": STR,
    "voice": STR,
    "avatar": "any",
    "system_prompt": STR,
    "seat": STR,
    "office_role": STR,
    "wants": "list[str]",
    "fears": "list[str]",
    "speech": STR,
    "relationships": "dict[str]",
    "knowledge": "list[str]",
    "turn_order_pos": INT,
}

PROFILE_SPEC = {
    "id": STR,
    "retains_fragments": BOOL,
    "is_main": BOOL,
    "identity": {
        "full_name": STR,
        "age": INT,
        "pronouns": STR,
        "title": STR,
        "seat": STR,
        "tenure_months": INT,
        "home": STR,
        "commute": STR,
        "hobbies": "list[str]",
    },
    "appearance": STR,
    "personality": {
        "traits": "list[str]",
        "speech_tics": "list[str]",
        "work_style": STR,
        "stress_response": STR,
    },
    "objectives": {
        "wants": "list[str]",
        "fears": "list[str]",
        "secrets": "list[str]",
    },
    "backstory": {
        "believed": STR,
        "truth": "str_or_empty",
    },
    "backstory_nodes": "list[node]",
    "behaviour_contract": "list[str]",
}

#: (path, min, max) list/dict size bounds — warnings only.
COUNT_BOUNDS_CAST = [("wants", 2, 4), ("fears", 2, 4), ("relationships", 3, None)]
COUNT_BOUNDS_PROFILE = [("personality.traits", 4, 6), ("backstory_nodes", 8, 20)]


# ---- helpers ---------------------------------------------------------------

def _load_yaml(path):
    """Load a YAML file. Returns (data, error_message)."""
    _trace("load_yaml enter path=%s", path)
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        log.error("run_id=%s op=load_yaml path=%s error=%s", RUN_ID, path, exc)
        return None, f"cannot parse {path.name}: {exc}"
    log.debug("run_id=%s op=load_yaml path=%s type=%s", RUN_ID, path, type(data).__name__)
    return data, None


def _type_ok(value, spec):
    if spec == "any":
        return True
    if spec == STR:
        return isinstance(value, str) and value.strip() != ""
    if spec == "str_or_empty":
        return value is None or isinstance(value, str)
    if spec == INT:
        return isinstance(value, int) and not isinstance(value, bool)
    if spec == BOOL:
        return isinstance(value, bool)
    if spec == "list[str]":
        return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)
    if spec == "dict[str]":
        return isinstance(value, dict) and all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items())
    if spec == "list[node]":
        return isinstance(value, list) and all(
            isinstance(n, dict) and isinstance(n.get("name"), str)
            and isinstance(n.get("statement"), str) and n["statement"].strip()
            for n in value)
    raise ValueError(f"unknown spec {spec!r}")


def _check_spec(data, spec, prefix, errors):
    """Check required keys and types of `data` against `spec`, appending errors."""
    for key, sub in spec.items():
        label = f"{prefix}{key}"
        if key not in data:
            errors.append(f"missing key '{label}'")
            continue
        value = data[key]
        if isinstance(sub, dict):
            if not isinstance(value, dict):
                errors.append(f"'{label}' must be a mapping")
                continue
            _check_spec(value, sub, label + ".", errors)
        elif not _type_ok(value, sub):
            errors.append(f"'{label}' has wrong type/shape (expected {sub}, "
                          f"got {type(value).__name__})")


def _get(data, dotted):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _word_count(text):
    return len(text.split()) if isinstance(text, str) else 0


def banned_hits(text):
    """Distinct banned words (lower-cased) found as whole words in `text`."""
    if not isinstance(text, str):
        return []
    return sorted({m.group(1).lower() for m in BANNED_RE.finditer(text)})


def load_voice_names(voices_path):
    """Return the set of voice keys under `voices:` in config/voices.yaml, or None on failure."""
    _trace("load_voice_names enter path=%s", voices_path)
    data, err = _load_yaml(pathlib.Path(voices_path))
    if err or not isinstance(data, dict) or not isinstance(data.get("voices"), dict):
        log.error("run_id=%s op=load_voices path=%s error=%s", RUN_ID, voices_path,
                  err or "no 'voices' mapping")
        return None
    names = set(data["voices"])
    log.debug("run_id=%s op=load_voices count=%d", RUN_ID, len(names))
    return names


# ---- per-file checks -------------------------------------------------------

def _check_cast(cid, cast, voice_names, node_names, errors, warnings):
    _trace("check_cast enter id=%s", cid)
    _check_spec(cast, CAST_SPEC, "", errors)
    role = OfficeRole(cid)

    if cast.get("office_role") != cid:
        valid = cast.get("office_role") in IDS
        errors.append(f"office_role {cast.get('office_role')!r} "
                      + ("does not match file id" if valid else "is not a valid OfficeRole"))
    if cast.get("seat") != SEAT[role]:
        errors.append(f"seat {cast.get('seat')!r} != {SEAT[role]!r}")
    seat_num = int(SEAT[role].rsplit("_", 1)[1])
    tpos = cast.get("turn_order_pos")
    if tpos != seat_num or isinstance(tpos, bool):
        errors.append(f"turn_order_pos {tpos!r} != seat number {seat_num}")

    voice = cast.get("voice")
    if voice_names is not None and voice not in voice_names:
        errors.append(f"voice {voice!r} not in config/voices.yaml voices")
    if voice != VOICE_FOR[cid]:
        errors.append(f"voice {voice!r} != schema voice {VOICE_FOR[cid]!r}")

    rels = cast.get("relationships")
    if isinstance(rels, dict):
        for key in rels:
            if key == cid:
                errors.append(f"relationships contains self key {key!r}")
            elif key not in IDS:
                errors.append(f"relationships key {key!r} is not a cast id")

    knowledge = cast.get("knowledge")
    if isinstance(knowledge, list) and node_names is not None:
        missing = [k for k in knowledge if k not in node_names]
        if missing:
            errors.append(f"knowledge not in backstory_nodes: {missing}")

    prompt = cast.get("system_prompt")
    if isinstance(prompt, str):
        if LOOP_SENTENCE not in prompt:
            errors.append(f"system_prompt lacks {LOOP_SENTENCE!r}")
        if cid == OfficeRole.PARTY_MEMBER.value and OBSERVER_SENTENCE not in prompt:
            errors.append(f"system_prompt lacks {OBSERVER_SENTENCE!r}")
        hits = banned_hits(prompt)
        if hits:
            errors.append(f"system_prompt contains banned words: {hits}")

    for path, lo, hi in COUNT_BOUNDS_CAST:
        _count_warning(cast, path, lo, hi, warnings)
    log.debug("run_id=%s op=check_cast id=%s errors=%d", RUN_ID, cid, len(errors))


def _check_profile(cid, prof, errors, warnings):
    _trace("check_profile enter id=%s", cid)
    _check_spec(prof, PROFILE_SPEC, "", errors)
    role = OfficeRole(cid)

    if prof.get("id") != cid:
        errors.append(f"profile id {prof.get('id')!r} != file/cast id {cid!r}")
    ident = prof.get("identity") if isinstance(prof.get("identity"), dict) else {}
    if "seat" in ident and ident["seat"] != SEAT[role]:
        errors.append(f"identity.seat {ident['seat']!r} != {SEAT[role]!r}")
    if "tenure_months" in ident and ident["tenure_months"] != TENURE_MONTHS[cid]:
        errors.append(f"identity.tenure_months {ident['tenure_months']!r} != lore "
                      f"{TENURE_MONTHS[cid]}")

    nodes = prof.get("backstory_nodes")
    if isinstance(nodes, list):
        seen = set()
        for node in nodes:
            name = node.get("name") if isinstance(node, dict) else None
            if not isinstance(name, str):
                continue
            if not NODE_NAME_RE.match(name):
                errors.append(f"node name {name!r} fails regex {NODE_NAME_RE.pattern}")
            elif name.split("-", 1)[0] not in VERBS:
                errors.append(f"node name {name!r} does not start with an allowlisted verb")
            if name in seen:
                errors.append(f"duplicate node name {name!r}")
            seen.add(name)

    believed = _get(prof, "backstory.believed")
    hits = banned_hits(believed)
    if hits:
        errors.append(f"backstory.believed contains banned words: {hits}")

    contract = prof.get("behaviour_contract")
    if isinstance(contract, list) and LOOP_SENTENCE not in contract:
        warnings.append(f"behaviour_contract lacks {LOOP_SENTENCE!r}")

    for path, lo, hi in COUNT_BOUNDS_PROFILE:
        _count_warning(prof, path, lo, hi, warnings)
    log.debug("run_id=%s op=check_profile id=%s errors=%d", RUN_ID, cid, len(errors))


def _count_warning(data, path, lo, hi, warnings):
    value = _get(data, path)
    if not isinstance(value, (list, dict)):
        return
    n = len(value)
    if n < lo or (hi is not None and n > hi):
        bound = f"{lo}-{hi}" if hi is not None else f">={lo}"
        warnings.append(f"'{path}' has {n} entries (expected {bound})")


def _word_warnings(cast, prof, warnings):
    sources = {
        "backstory.believed": _get(prof, "backstory.believed") if prof else None,
        "appearance": prof.get("appearance") if prof else None,
        "system_prompt": cast.get("system_prompt") if cast else None,
    }
    for label, (lo, hi) in WORD_BOUNDS.items():
        text = sources[label]
        if not isinstance(text, str):
            continue
        n = _word_count(text)
        if not lo <= n <= hi:
            log.debug("run_id=%s op=word_bounds field=%s words=%d", RUN_ID, label, n)
            warnings.append(f"{label} is {n} words (expected {lo}-{hi})")


# ---- entry points ----------------------------------------------------------

def validate_character(pack_dir, cid, voice_names):
    """Validate one character. Returns {"errors": [...], "warnings": [...]}."""
    _trace("validate_character enter id=%s", cid)
    errors, warnings = [], []
    cast_path = pack_dir / "cast" / f"{cid}.yaml"
    prof_path = pack_dir / "profiles" / f"{cid}.yaml"
    cast = prof = None

    for path, kind in ((cast_path, "cast"), (prof_path, "profile")):
        if not path.exists():
            log.debug("run_id=%s op=exists id=%s kind=%s present=false", RUN_ID, cid, kind)
            errors.append(f"{kind} file missing: {path.relative_to(pack_dir).as_posix()}")
            continue
        data, err = _load_yaml(path)
        if err:
            errors.append(err)
        elif not isinstance(data, dict):
            errors.append(f"{kind} file is not a mapping")
        elif kind == "cast":
            cast = data
        else:
            prof = data

    node_names = None
    if prof is not None:
        _check_profile(cid, prof, errors, warnings)
        nodes = prof.get("backstory_nodes")
        if isinstance(nodes, list):
            node_names = {n.get("name") for n in nodes if isinstance(n, dict)}
    if cast is not None:
        _check_cast(cid, cast, voice_names, node_names, errors, warnings)
    _word_warnings(cast, prof, warnings)
    _trace("validate_character exit id=%s errors=%d warnings=%d", cid, len(errors), len(warnings))
    return {"errors": errors, "warnings": warnings}


def validate_pack(pack_dir, voices_path=None):
    """Validate all 8 office characters in `pack_dir`. Returns a report dict."""
    pack_dir = pathlib.Path(pack_dir).resolve()
    voices_path = pathlib.Path(voices_path) if voices_path else REPO / "config" / "voices.yaml"
    _trace("validate_pack enter pack=%s voices=%s", pack_dir, voices_path)

    pack_errors = []
    voice_names = load_voice_names(voices_path)
    if voice_names is None:
        pack_errors.append(f"cannot read voices registry {voices_path}")

    characters = {cid: validate_character(pack_dir, cid, voice_names) for cid in IDS}

    for kind in ("cast", "profiles"):
        d = pack_dir / kind
        if d.is_dir():
            for extra in sorted(p.stem for p in d.glob("*.yaml")
                                if p.stem not in IDS and not p.stem.startswith("_")):
                pack_errors.append(f"{kind}/{extra}.yaml is not an office cast id")

    n_err = len(pack_errors) + sum(len(c["errors"]) for c in characters.values())
    n_warn = sum(len(c["warnings"]) for c in characters.values())
    report = {
        "pack": str(pack_dir),
        "ok": n_err == 0,
        "errors": n_err,
        "warnings": n_warn,
        "pack_errors": pack_errors,
        "characters": characters,
    }
    log.info("run_id=%s op=validate_pack pack=%s ok=%s errors=%d warnings=%d",
             RUN_ID, pack_dir, report["ok"], n_err, n_warn)
    return report


def format_report(report):
    """Human-readable per-character report."""
    lines = [f"Office profile validation: {report['pack']}"]
    for msg in report["pack_errors"]:
        lines.append(f"  [pack] ERROR {msg}")
    for cid, res in report["characters"].items():
        status = "OK" if not res["errors"] else "FAIL"
        lines.append(f"{cid:<15} {status}  ({len(res['errors'])} errors, "
                     f"{len(res['warnings'])} warnings)")
        lines.extend(f"    ERROR {m}" for m in res["errors"])
        lines.extend(f"    WARN  {m}" for m in res["warnings"])
    lines.append(f"Result: {'PASS' if report['ok'] else 'FAIL'} — "
                 f"{report['errors']} errors, {report['warnings']} warnings")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate ashiorid_office cast + profile files against profiles/_SCHEMA.md.")
    parser.add_argument("--pack", default=str(REPO / "campaigns" / "ashiorid_office"),
                        help="campaign pack directory (default: campaigns/ashiorid_office)")
    parser.add_argument("--voices", default=None,
                        help="voice registry (default: config/voices.yaml)")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)
    _trace("main enter pack=%s json=%s", args.pack, args.json)

    report = validate_pack(args.pack, args.voices)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    sys.exit(main())
