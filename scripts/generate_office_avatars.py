#!/usr/bin/env python3
"""
scripts/generate_office_avatars.py
OB-20: backstory -> avatar for the ashiorid_office cast.

For each cast member it reads profiles/<id>.yaml (appearance + personality),
maps it to codec-head sliders with character.avatar.map_appearance, then:

  1. writes a `character_params:` block into cast/<id>.yaml (only that key;
     the rest of the file, comments included, is left as-is),
  2. prints the roster snippet for the office roundtable config,
  3. renders one preview PNG per character (front | three-quarter, side by
     side) into preview_out/office/<id>.png.

Two sources for the LLM step:

  live (default)   build_llm_client() from --config (a worker config's `llm:`
                   block; LLM_PROVIDER / LLM_BASE_URL env vars override it,
                   as everywhere else), optionally --base-url / --model.
  --params-file    a JSON object {cast_id: reply} of canned model replies,
                   replayed through the same parse/clamp/validate path. Use
                   when no model is reachable.

Run:
    .venv/bin/python scripts/generate_office_avatars.py \
        --params-file campaigns/ashiorid_office/profiles/_avatar_params.json
    .venv/bin/python scripts/generate_office_avatars.py \
        --base-url http://192.168.1.23:11434 --model qwen2.5:7b-instruct-q4_K_M

See docs/character_avatar.md.
"""
import argparse
import json
import logging
import pathlib
import re
import sys
import uuid

import numpy as np
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO / "app") not in sys.path:
    sys.path.insert(0, str(REPO / "app"))

from character.avatar import ReplayLLMClient, map_appearance_result  # noqa: E402
from character_schema import PARAM_DEFAULTS, SLIDER_DEFAULTS  # noqa: E402

log = logging.getLogger("generate_office_avatars")
TRACE = 5
logging.addLevelName(TRACE, "TRACE")
RUN_ID = uuid.uuid4().hex[:8]

DEFAULT_PACK = REPO / "campaigns" / "ashiorid_office"
DEFAULT_CONFIG = REPO / "config" / "workers" / "roundtable.yaml"
DEFAULT_OUT_DIR = REPO / "preview_out" / "office"

#: The two views composed into each preview PNG.
PREVIEW_VIEWS = [("front", 0.0), ("three-quarter", 0.9)]

KEY_ORDER = list(SLIDER_DEFAULTS) + ["accent_color"]
CHARACTER_PARAMS_COMMENT = "# OB-20: scripts/generate_office_avatars.py"


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, "run_id=%s " + msg, RUN_ID, *args)


def load_cast_ids(pack):
    """Cast ids in seat order, from campaign.yaml `seats:`."""
    _trace("op=load_cast_ids enter pack=%s", pack)
    campaign = yaml.safe_load((pack / "campaign.yaml").read_text(encoding="utf-8"))
    seats = campaign.get("seats") or {}
    if not seats:
        raise SystemExit(f"error: {pack}/campaign.yaml has no seats:")
    ids = sorted(seats, key=lambda cid: int(str(seats[cid]).rsplit("_", 1)[-1]))
    _trace("op=load_cast_ids exit ids=%s", ids)
    return ids, seats


def load_params_file(path):
    """{cast_id: reply} from a JSON file; keys starting with '_' are notes."""
    log.debug("run_id=%s op=load_params_file path=%s", RUN_ID, path)
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("run_id=%s op=load_params_file path=%s error=%s", RUN_ID, path, exc)
        raise SystemExit(f"error: cannot read --params-file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("error: --params-file must hold a JSON object {cast_id: reply}")
    return {k: v for k, v in data.items() if not k.startswith("_")}


def build_live_client(config_path, base_url, model):
    """An app/llm_client.py client from a worker config, with CLI overrides."""
    from llm_client import build_llm_client  # lazy: pulls in httpx/anthropic

    config = yaml.safe_load(pathlib.Path(config_path).read_text(encoding="utf-8")) or {}
    llm = dict(config.get("llm") or {})
    if base_url:
        llm["base_url"] = base_url
    if model:
        llm["model"] = model
    # JSON extraction wants a cool, short answer.
    llm["temperature"] = min(float(llm.get("temperature", 0.3)), 0.3)
    llm["max_tokens"] = min(int(llm.get("max_tokens", 400)), 400)
    log.info("run_id=%s op=build_live_client provider=%s base_url=%s model=%s",
             RUN_ID, llm.get("provider", "ollama"), llm.get("base_url"), llm.get("model"))
    return build_llm_client({"llm": llm})


def format_character_params(params, source):
    """The YAML block written into a cast file, in a stable key order."""
    lines = [f"character_params:  {CHARACTER_PARAMS_COMMENT} (source: {source})"]
    for key in KEY_ORDER:
        value = params[key]
        lines.append(f"  {key}: {value:.2f}" if key in SLIDER_DEFAULTS else f"  {key}: {value}")
    return "\n".join(lines) + "\n"


_BLOCK_RE = re.compile(r"^character_params:.*\n(?:[ \t]+.*\n|[ \t]*\n(?=[ \t]))*", re.M)
_AVATAR_RE = re.compile(r"^avatar:.*\n", re.M)


def write_character_params(cast_path, params, source):
    """Insert or replace the top-level `character_params:` block in place.

    Text-level edit so comments, quoting and key order elsewhere survive
    (a yaml round-trip would reflow every block scalar). The new block goes
    right after `avatar:` the first time; later runs replace it. The result
    is re-parsed and checked: every other key must be unchanged.
    """
    _trace("op=write_character_params enter path=%s", cast_path)
    text = cast_path.read_text(encoding="utf-8")
    before = yaml.safe_load(text)
    block = format_character_params(params, source)
    if _BLOCK_RE.search(text):
        log.debug("run_id=%s op=write_character_params path=%s action=replace", RUN_ID, cast_path)
        new_text = _BLOCK_RE.sub(lambda _m: block, text, count=1)
    elif (match := _AVATAR_RE.search(text)):
        log.debug("run_id=%s op=write_character_params path=%s action=insert_after_avatar",
                  RUN_ID, cast_path)
        new_text = text[:match.end()] + block + text[match.end():]
    else:
        log.debug("run_id=%s op=write_character_params path=%s action=append", RUN_ID, cast_path)
        new_text = text + ("" if text.endswith("\n") else "\n") + block

    after = yaml.safe_load(new_text)
    before.pop("character_params", None)
    written = after.pop("character_params", None)
    if after != before or written != params:
        log.error("run_id=%s op=write_character_params path=%s error=roundtrip_mismatch",
                  RUN_ID, cast_path)
        raise RuntimeError(f"refusing to write {cast_path}: edit changed other keys")
    cast_path.write_text(new_text, encoding="utf-8")
    log.info("run_id=%s op=write_character_params path=%s source=%s", RUN_ID, cast_path, source)


def roster_snippet(rows):
    """Roster lines in config/workers/roundtable.yaml's mapping form."""
    out = ["roster:"]
    for row in rows:
        p = row["params"]
        inline = ", ".join(
            f"{k}: {p[k]:.2f}" if k in SLIDER_DEFAULTS else f"{k}: {p[k]}" for k in KEY_ORDER)
        out.append(f'  {row["seat"]}: {{name: "{row["name"]}", '
                   f'character_params: {{{inline}}}}}  # {row["id"]}')
    return "\n".join(out)


def render_preview(params, out_path, force_cpu=False):
    """One PNG per character: front and three-quarter views side by side."""
    import character_preview as cp
    from pixel_raster import TINT_CODEC_GREEN, write_png

    log.debug("run_id=%s op=render_preview path=%s", RUN_ID, out_path)
    frames = cp.render_views(params, PREVIEW_VIEWS, cp.WIDTH, cp.HEIGHT, cp.DIST,
                             tint=TINT_CODEC_GREEN, force_cpu=force_cpu)
    image = np.concatenate([img for _label, img, _dt, _backend in frames], axis=1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_png(str(out_path), image)
    log.info("run_id=%s op=render_preview path=%s backend=%s", RUN_ID, out_path, frames[0][3])
    return out_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="OB-20: office cast appearance -> codec-head params.")
    parser.add_argument("--pack", default=str(DEFAULT_PACK))
    parser.add_argument("--params-file",
                        help="JSON {cast_id: reply}: replay canned LLM replies instead of calling a model")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG),
                        help="worker config whose llm: block builds the live client")
    parser.add_argument("--base-url", help="override llm.base_url (live path)")
    parser.add_argument("--model", help="override llm.model (live path)")
    parser.add_argument("--only", action="append", metavar="CAST_ID",
                        help="process just this cast id; repeatable")
    parser.add_argument("--no-write", action="store_true", help="do not modify cast YAMLs")
    parser.add_argument("--no-render", action="store_true", help="skip the preview PNGs")
    parser.add_argument("--cpu", action="store_true", help="force the numpy renderer")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=TRACE if args.verbose > 1 else logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s", stream=sys.stderr)

    pack = pathlib.Path(args.pack)
    ids, seats = load_cast_ids(pack)
    if args.only:
        unknown = sorted(set(args.only) - set(ids))
        if unknown:
            raise SystemExit(f"error: unknown cast id(s) {unknown}")
        ids = [i for i in ids if i in args.only]

    replies = load_params_file(args.params_file) if args.params_file else None
    if replies is not None:
        missing = [i for i in ids if i not in replies]
        if missing:
            raise SystemExit(f"error: --params-file has no reply for {missing}")
        source = "params-file"
        live_client = None
    else:
        source = "llm"
        live_client = build_live_client(args.config, args.base_url, args.model)

    rows = []
    fallbacks = []
    for cast_id in ids:
        profile = yaml.safe_load((pack / "profiles" / f"{cast_id}.yaml").read_text(encoding="utf-8"))
        cast_path = pack / "cast" / f"{cast_id}.yaml"
        cast = yaml.safe_load(cast_path.read_text(encoding="utf-8"))
        client = ReplayLLMClient(replies[cast_id]) if replies is not None else live_client
        result = map_appearance_result(profile, client, request_id=f"{RUN_ID}-{cast_id}")
        row_source = source if result.source == "llm" else "default"
        if result.source == "default":
            fallbacks.append(cast_id)
        if not args.no_write:
            write_character_params(cast_path, result.params, row_source)
        png = None
        if not args.no_render:
            png = render_preview(result.params, pathlib.Path(args.out_dir) / f"{cast_id}.png",
                                 force_cpu=args.cpu)
        rows.append({"id": cast_id, "name": cast["name"], "seat": seats[cast_id],
                     "params": result.params, "source": row_source, "png": png})

    print("# ---- roster snippet for the office roundtable config ----")
    print(roster_snippet(rows))
    print()
    for row in rows:
        print(f"{row['id']:>15}: source={row['source']}"
              + (f"  png={row['png']}" if row["png"] else ""))
    if fallbacks:
        print(f"WARNING: fell back to defaults for {fallbacks}", file=sys.stderr)
    log.info("run_id=%s op=generate_office_avatars count=%d fallbacks=%d",
             RUN_ID, len(rows), len(fallbacks))
    return 1 if fallbacks else 0


if __name__ == "__main__":
    sys.exit(main())
