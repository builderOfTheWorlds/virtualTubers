"""
primitives.py
The campaign-platform rendering engine: turns a scene's declarative
`render[]` entries (episode_schema.py's schema — {primitive, payload,
target}) into paced, colorized output, and estimates how long they take on
screen before anything is actually performed.

Three layers, each owned by a different party (docs/campaign_platform_build.md):
    Behaviors   (BEHAVIORS, in code)          -- type / print / diff / image / pause
    Primitives  (config/primitives.yaml +
                 config/campaigns/<c>/primitives.yaml)  -- named recipes over behaviors
    Scenes      (the episode file)            -- kind + render[]

This module owns the middle-and-code layers only: resolving a campaign's
merged primitive table (load_primitives), looking up one recipe
(resolve_recipe), estimating screen time before anything airs
(estimate_entry_seconds / estimate_scene_seconds), a short human/LLM-facing
description of a scene's visuals (render_summary), and actually running one
render entry's behaviors against a RenderContext (perform_entry).

It never touches an episode's `text`/narration/audio fields, never decides
`mode` (that's the scene's own field, read by estimate_scene_seconds and by
replay.py's Performer._perform_render), and never imports replay.py itself
(RenderContext is a plain structural facade replay.py constructs and passes
in, so a Performer's Pacer/Palette/agent_state never have to be imported
here) -- see CONTRACT.md §2.

Two-layer merge: mirrors build_layout.deep_merge EXACTLY (dicts merge
recursively, scalars AND lists are full replaces, neither input is mutated)
-- imported from build_layout rather than re-implemented, since it is
already tested there.
"""
import logging
import re
from pathlib import Path

import yaml

from build_layout import deep_merge

try:
    from PIL import Image
except ImportError:  # Pillow is optional -- see the `image` behavior below.
    Image = None


log = logging.getLogger("primitives")

# The five behaviors that exist in code. Adding one is the one thing that
# needs a code change (docs/campaign_platform_build.md); the primitive
# (config) layer is meant to absorb everything else.
BEHAVIORS = {"type", "print", "diff", "image", "pause"}

# A literal `{dotted.path}` token inside a recipe's `text`/`action` template,
# resolved against the render entry ({primitive, payload, target}) it is
# performing -- e.g. "{payload.file}", "{payload.tool}".
_TOKEN_RE = re.compile(r"\{([A-Za-z0-9_.]+)\}")

# Palette attribute names style dicts may reference under `fg`/`add`/`remove`
# (see _color_attr) -- exactly app/replay.py's Palette surface, so a
# misconfigured/unknown color name degrades to "no color" instead of raising.
_PALETTE_COLOR_NAMES = frozenset({"cyan", "green", "yellow", "red", "magenta"})


class PrimitiveError(Exception):
    """Raised for anything that makes a campaign's primitive table
    unusable: a missing/malformed base config, an unknown primitive name
    looked up by resolve_recipe, or an `extends` chain that is broken
    (unknown parent) or cyclic."""


# ── RenderContext (constructed by replay.py, consumed here) ──────────────────

class RenderContext:
    """Everything one perform_entry() call needs from the performer, without
    importing replay.py's Pacer/Palette/agent_state directly.

    write(text, *, target=None)  -- routes text to a sink (replay.py owns the
                                     sink table; an unresolvable target is
                                     replay.py's problem, not ours).
    pacer     -- replay.Pacer:    .sleep(seconds) / .type_out(write, text, cps)
                                     / .check_stop().
    palette   -- replay.Palette: .reset/.dim/.bold/.cyan/.green/.yellow/.red/
                                     .magenta (all "" when colors are disabled).
    avatar(expression, action="", bubble=None) -- never raises (mirrors
                                     Performer._avatar's own contract).
    max_output_lines -- default truncation cap when a behavior doesn't set
                                     its own `max_lines`.
    """

    def __init__(self, write, pacer, palette, avatar, max_output_lines=24):
        self.write = write
        self.pacer = pacer
        self.palette = palette
        self.avatar = avatar
        self.max_output_lines = max_output_lines


# ── Config loading + merge ────────────────────────────────────────────────────

def _default_base_path():
    in_container = Path("/config/primitives.yaml")
    if in_container.is_file():
        return in_container
    return Path("config") / "primitives.yaml"


def _default_campaigns_dir():
    in_container = Path("/config/campaigns")
    if in_container.is_dir():
        return in_container
    return Path("config") / "campaigns"


def _load_yaml_or_raise(path, context):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as exc:
        log.error("failed to read %s at %s: %s", context, path, exc)
        raise PrimitiveError(f"failed to read {context} at {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        log.error("failed to parse %s at %s: %s", context, path, exc)
        raise PrimitiveError(f"failed to parse {context} at {path}: {exc}") from exc
    return data or {}


def _resolve_extends_chain(name, raw_table, base_cache, resolved_cache, visiting):
    """Resolve one primitive's full `extends` chain, memoized across the
    whole load_primitives() call.

    `base_cache` is the SHARED (base-layer-only) table, already fully
    resolved on its own before `raw_table` (base deep-merged with the
    campaign overlay) is built -- see load_primitives. It exists for one
    reason: a campaign primitive that shares its NAME with a shared one and
    writes `extends: <that same name>` (the intentional-specialization form
    the "redefines a shared primitive without extends" warning exempts)
    would otherwise resolve its own parent as itself, since `raw_table[name]`
    IS the campaign's overlay by the time extends resolution runs -- a false
    cycle, not a real one. `base_cache[name]` gives that lookup somewhere
    real to land: the shared definition as authored, one layer down.

    `visiting` tracks the names currently being resolved THROUGH raw_table
    on this call stack, so a genuine cross-primitive cycle (A extends B
    extends A) raises PrimitiveError instead of recursing forever. The
    same-name self-extends case never touches `visiting` at all -- it
    can't recurse into a longer chain, so it can't cycle.
    """
    if name in resolved_cache:
        return resolved_cache[name]

    raw = raw_table.get(name)
    if raw is None:
        raise PrimitiveError(f"primitive {name!r} not found (broken 'extends' reference)")
    if not isinstance(raw, dict):
        raise PrimitiveError(f"primitive {name!r} must be a mapping, got {type(raw).__name__}")

    parent_name = raw.get("extends")
    if not parent_name:
        resolved = {k: v for k, v in raw.items() if k != "extends"}
        resolved_cache[name] = resolved
        return resolved

    if parent_name == name:
        parent = base_cache.get(name)
        if parent is None:
            raise PrimitiveError(
                f"primitive {name!r} extends itself with no shared base version to inherit from"
            )
        resolved = _merge_recipe(parent, raw)
        resolved_cache[name] = resolved
        return resolved

    if name in visiting:
        chain = " -> ".join(list(visiting) + [name])
        log.error("extends cycle detected: %s", chain)
        raise PrimitiveError(f"extends cycle detected: {chain}")

    visiting.add(name)
    try:
        parent = _resolve_extends_chain(parent_name, raw_table, base_cache, resolved_cache, visiting)
        resolved = _merge_recipe(parent, raw)
    finally:
        visiting.discard(name)

    resolved_cache[name] = resolved
    return resolved


def _merge_recipe(parent, child):
    """Apply a child recipe on top of its resolved parent. Every field
    except `behaviors` uses deep_merge (dicts recurse, scalars/lists
    replace) exactly like build_layout's own layers; `behaviors` merges
    positionally instead (see _merge_behaviors_positional) -- the design
    doc's stated choice for `extends`, which the doc itself flags as
    ambiguous for multi-behavior recipes: a positional index is unambiguous
    for the single-behavior specializations shipped today (display_map,
    scene_heading), but a child wanting to edit behaviors[2] of a 5-behavior
    parent without touching 0/1 would need padding entries -- there is no
    named-slot addressing.
    """
    child_rest = {k: v for k, v in child.items() if k not in ("extends", "behaviors")}
    merged = deep_merge(parent, child_rest)
    merged["behaviors"] = _merge_behaviors_positional(
        parent.get("behaviors") or [], child.get("behaviors")
    )
    return merged


def _merge_behaviors_positional(parent_behaviors, child_behaviors):
    if not child_behaviors:
        return list(parent_behaviors)
    result = []
    for i in range(max(len(parent_behaviors), len(child_behaviors))):
        if i < len(parent_behaviors) and i < len(child_behaviors):
            result.append(deep_merge(parent_behaviors[i], child_behaviors[i]))
        elif i < len(parent_behaviors):
            result.append(parent_behaviors[i])
        else:
            result.append(child_behaviors[i])
    return result


def load_primitives(campaign, *, base_path=None, campaigns_dir=None):
    """Merged, fully-resolved recipe table for one campaign.

    Loads config/primitives.yaml (shared), then deep-merges
    config/campaigns/<campaign>/primitives.yaml over it (build_layout.deep_merge
    semantics, imported not re-implemented), then resolves every `extends`
    chain. Logs a WARNING when a campaign redefines a shared primitive
    (same top-level key) without declaring `extends` on it -- an explicit
    `extends` is treated as intentional specialization and never warns.

    Missing campaign file is fine (shared-only campaign). Missing/malformed
    base file raises PrimitiveError -- there is no sensible default for the
    shared layer.
    """
    base_path = Path(base_path) if base_path is not None else _default_base_path()
    campaigns_dir = Path(campaigns_dir) if campaigns_dir is not None else _default_campaigns_dir()

    base_table = _load_yaml_or_raise(base_path, "base primitives")

    campaign_path = Path(campaigns_dir) / campaign / "primitives.yaml"
    if campaign_path.is_file():
        campaign_table = _load_yaml_or_raise(campaign_path, f"campaign {campaign!r} primitives")
    else:
        log.debug("no campaign primitives file at %s; using shared primitives only", campaign_path)
        campaign_table = {}

    for name, raw in campaign_table.items():
        extends = isinstance(raw, dict) and raw.get("extends")
        if name in base_table and not extends:
            log.warning(
                "campaign %r primitive %r redefines a shared primitive without 'extends' "
                "(silent overrides are confusing -- add 'extends: %s' if that's intentional)",
                campaign, name, name,
            )

    # Resolve the shared layer fully on its own first -- this becomes the
    # fallback lookup table for a same-name "extends: <own name>" campaign
    # primitive (see _resolve_extends_chain's docstring). Base primitives
    # never extend anything today, but this stays correct even if one did.
    base_cache = {}
    for name in base_table:
        _resolve_extends_chain(name, base_table, base_cache, base_cache, set())

    raw_table = deep_merge(base_table, campaign_table)

    resolved_cache = {}
    result = {}
    for name in raw_table:
        result[name] = _resolve_extends_chain(name, raw_table, base_cache, resolved_cache, set())
    # DEBUG, not INFO: this is deliberately re-read per airing (so editing a
    # primitives.yaml takes effect on the next show with no restart), which
    # made an INFO line here fire on every single request.
    log.debug("loaded %d primitives for campaign %r (%d shared, %d campaign)",
              len(result), campaign, len(base_table), len(campaign_table))
    return result


def resolve_recipe(primitives, name):
    """Fully-resolved recipe dict. Raises PrimitiveError on unknown name."""
    try:
        return primitives[name]
    except KeyError:
        raise PrimitiveError(f"unknown primitive: {name!r}") from None
    except TypeError:
        raise PrimitiveError(f"primitives table is not a mapping (got {type(primitives).__name__})") from None


# ── Field / token resolution ──────────────────────────────────────────────────

def _resolve_field(entry, path):
    """Safe dotted-path getter into a render entry ({primitive, payload,
    target}). Returns None on any missing segment or non-dict intermediate
    -- callers treat None as "renders nothing, costs 0 seconds"."""
    if not path:
        return None
    value = entry
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None
    return value


def _format_literal(template, entry):
    """Substitute `{dotted.path}` tokens in a recipe's literal text/action
    template against the render entry. An unresolvable token becomes an
    empty string rather than raising or leaving the brace behind -- these
    are decorative headings/avatar actions, not user-facing errors."""
    if not template:
        return ""

    def repl(match):
        value = _resolve_field(entry, match.group(1))
        return "" if value is None else str(value)

    return _TOKEN_RE.sub(repl, template)


def _display_lines(content, max_lines):
    """Split `content` (a string, or already a list of lines -- e.g.
    payload.hunks) into (displayed_lines, hidden_count), truncated at
    max_lines exactly like replay._truncate_lines does today."""
    if isinstance(content, list):
        lines = [str(x) for x in content]
    else:
        lines = str(content).splitlines()
    if max_lines is None or len(lines) <= max_lines:
        return lines, 0
    return lines[:max_lines], len(lines) - max_lines


def _split_diff_lines(lines):
    """Partition a `diff` behavior's field (a list of '+'/'-' prefixed
    lines) into (removed, added), each with its marker+space stripped and
    order preserved. See config/campaigns/coder/primitives.yaml's payload
    contract comment: the coder generator emits every old-file line marked
    '-' followed by every new-file line marked '+', which is what lets this
    reproduce Edit's old-scroll/new-type split exactly."""
    removed, added = [], []
    for raw in lines:
        line = str(raw)
        if line.startswith("- "):
            removed.append(line[2:])
        elif line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+ "):
            added.append(line[2:])
        elif line.startswith("+"):
            added.append(line[1:])
        else:
            added.append(line)
    return removed, added


# ── Screen-time estimation ────────────────────────────────────────────────────

def _behavior_seconds(behavior, entry, default_max_lines):
    kind = behavior.get("behavior")
    if kind == "pause":
        return max(float(behavior.get("seconds", 0.0)), 0.0)
    if kind == "image":
        return max(float(behavior.get("hold_s", 0.0)), 0.0)
    if "text" in behavior:
        return 0.0  # literal/decorative content is always instant (0 cost)

    content = _resolve_field(entry, behavior.get("field"))
    if content is None:
        return 0.0
    max_lines = behavior.get("max_lines", default_max_lines)

    if kind == "type":
        rate = behavior.get("rate_cps") or 0
        if rate <= 0:
            return 0.0
        lines, _ = _display_lines(content, max_lines)
        return sum(len(line) for line in lines) / rate

    if kind == "print":
        rate = behavior.get("rate_lps") or 0
        if rate <= 0:
            return 0.0
        lines, _ = _display_lines(content, max_lines)
        return len(lines) / rate

    if kind == "diff":
        lines = content if isinstance(content, list) else str(content).splitlines()
        removed_raw, added_raw = _split_diff_lines(lines)
        removed, _ = _display_lines(removed_raw, max_lines)
        added, _ = _display_lines(added_raw, max_lines)
        seconds = 0.0
        rate_lps = behavior.get("rate_lps") or 0
        rate_cps = behavior.get("rate_cps") or 0
        if rate_lps > 0:
            seconds += len(removed) / rate_lps
        if rate_cps > 0:
            seconds += sum(len(line) for line in added) / rate_cps
        return seconds

    log.debug("unknown behavior kind %r in recipe; treated as 0 seconds", kind)
    return 0.0


def estimate_entry_seconds(entry, primitives, *, max_output_lines=24):
    """Screen-time for ONE render[] entry (a {primitive, payload, target}
    dict) at speed 1.0 -- sums every behavior's own timing formula (see the
    behavior table in CONTRACT.md §2 / docs/primitives.md)."""
    recipe = resolve_recipe(primitives, entry.get("primitive"))
    return sum(
        _behavior_seconds(behavior, entry, max_output_lines)
        for behavior in recipe.get("behaviors", [])
    )


def estimate_scene_seconds(scene, primitives, *, max_output_lines=24, speed=1.0):
    """Screen-time for a whole scene. SUM under mode 'sequence' (the
    default), MAX under 'parallel' -- mirrors how replay.py will run
    render[] entries one after another vs. interleaved via threads.
    Divided by max(speed, 0.01). A scene with render:[] is 0.0."""
    render = scene.get("render") or []
    if not render:
        return 0.0
    per_entry = [
        estimate_entry_seconds(entry, primitives, max_output_lines=max_output_lines)
        for entry in render
    ]
    mode = scene.get("mode", "sequence")
    total = max(per_entry) if mode == "parallel" else sum(per_entry)
    return total / max(speed, 0.01)


# ── render_summary (narration.yaml's {render_summary} token) ────────────────

def _first_text_snippet(payload):
    """First short, human-readable string value in a payload dict -- best
    effort only, used purely to give an LLM/fallback-template a hint of
    what's on screen without primitives.py needing to know any campaign's
    domain (render_summary must stay presentation-neutral)."""
    if not isinstance(payload, dict):
        return ""
    for value in payload.values():
        if isinstance(value, str) and value.strip():
            snippet = " ".join(value.split())
            return snippet[:60] + ("…" if len(snippet) > 60 else "")
    return ""


def render_summary(scene, primitives):
    """Short human/LLM-facing description of what a scene shows on screen.
    Feeds narration.yaml's {material} and {render_summary} tokens. Never
    raises -- a broken recipe reference or malformed entry just gets
    skipped, since this is a narration hint, not a validated render."""
    parts = []
    for entry in scene.get("render") or []:
        try:
            name = entry.get("primitive", "?") if isinstance(entry, dict) else "?"
            snippet = _first_text_snippet(entry.get("payload")) if isinstance(entry, dict) else ""
        except Exception:
            log.debug("render_summary: skipping malformed render entry", exc_info=True)
            continue
        parts.append(f"{name}: {snippet}" if snippet else name)
    return "; ".join(parts)


# ── Styling ────────────────────────────────────────────────────────────────

def _color_attr(palette, name):
    """A Palette attribute by name, or "" if unset/unknown -- a style dict
    referencing a color the Palette doesn't have (e.g. config/primitives.yaml's
    own `fg: white`, from the design doc verbatim) degrades to no color
    rather than raising."""
    if not name or name not in _PALETTE_COLOR_NAMES:
        return ""
    return getattr(palette, name, "") or ""


def _style_codes(palette, style):
    """`style` maps to Palette attribute names, e.g. {fg: green, bold: true}
    (CONTRACT.md §2) -- used by type/print behaviors. `diff`'s {add, remove}
    shape is handled separately by _perform_diff via _color_attr directly."""
    if not style:
        return ""
    codes = [_color_attr(palette, style.get("fg"))]
    if style.get("bold"):
        codes.append(palette.bold)
    if style.get("dim"):
        codes.append(palette.dim)
    return "".join(codes)


# ── Behavior execution ────────────────────────────────────────────────────────

def _write_literal(behavior, entry, ctx, target):
    text = _format_literal(behavior.get("text", ""), entry)
    style = _style_codes(ctx.palette, behavior.get("style"))
    reset = ctx.palette.reset
    lines = text.splitlines() or [""]
    for line in lines:
        ctx.write(f"{style}{line}{reset}\n" if style else f"{line}\n", target=target)


def _perform_type(behavior, content, ctx, target, max_lines):
    displayed, hidden = _display_lines(content, max_lines)
    prefix = behavior.get("prefix", "")
    continuation_prefix = behavior.get("continuation_prefix", prefix)
    prefix_style = _style_codes(ctx.palette, behavior.get("prefix_style"))
    continuation_style = _style_codes(
        ctx.palette, behavior.get("continuation_prefix_style", behavior.get("prefix_style"))
    )
    content_style = _style_codes(ctx.palette, behavior.get("style"))
    rate = behavior.get("rate_cps") or 0
    reset = ctx.palette.reset

    def _write_target(text):
        ctx.write(text, target=target)

    for i, line in enumerate(displayed):
        p, p_style = (prefix, prefix_style) if i == 0 else (continuation_prefix, continuation_style)
        if p:
            ctx.write(f"{p_style}{p}{reset}" if p_style else p, target=target)
        if content_style:
            ctx.write(content_style, target=target)
        if rate > 0:
            ctx.pacer.type_out(_write_target, line, rate)
        else:
            ctx.write(line, target=target)
        if content_style:
            ctx.write(reset, target=target)
        ctx.write("\n", target=target)
    if hidden:
        ctx.write(f"{ctx.palette.dim}… ({hidden} more lines){reset}\n", target=target)


def _perform_print(behavior, content, ctx, target, max_lines):
    displayed, hidden = _display_lines(content, max_lines)
    prefix = behavior.get("prefix", "")
    style = _style_codes(ctx.palette, behavior.get("style"))
    reset = ctx.palette.reset
    rate = behavior.get("rate_lps") or 0
    for line in displayed:
        text = f"{prefix}{line}"
        ctx.write(f"{style}{text}{reset}\n" if style else f"{text}\n", target=target)
        if rate > 0:
            ctx.pacer.sleep(1.0 / rate)
    if hidden:
        ctx.write(f"{ctx.palette.dim}… ({hidden} more lines){reset}\n", target=target)


def _perform_diff(behavior, content, ctx, target, max_lines):
    lines = content if isinstance(content, list) else str(content).splitlines()
    removed_raw, added_raw = _split_diff_lines(lines)
    removed, removed_hidden = _display_lines(removed_raw, max_lines)
    added, added_hidden = _display_lines(added_raw, max_lines)
    style = behavior.get("style") or {}
    remove_style = _color_attr(ctx.palette, style.get("remove"))
    add_style = _color_attr(ctx.palette, style.get("add"))
    reset = ctx.palette.reset
    rate_lps = behavior.get("rate_lps") or 0
    rate_cps = behavior.get("rate_cps") or 0

    for line in removed:
        text = f"- {line}"
        ctx.write(f"{remove_style}{text}{reset}\n" if remove_style else f"{text}\n", target=target)
        if rate_lps > 0:
            ctx.pacer.sleep(1.0 / rate_lps)
    if removed_hidden:
        ctx.write(f"{ctx.palette.dim}… ({removed_hidden} more lines){reset}\n", target=target)

    def _write_target(text):
        ctx.write(text, target=target)

    for line in added:
        if add_style:
            ctx.write(f"{add_style}+ {reset}", target=target)
            ctx.write(add_style, target=target)
        else:
            ctx.write("+ ", target=target)
        if rate_cps > 0:
            ctx.pacer.type_out(_write_target, line, rate_cps)
        else:
            ctx.write(line, target=target)
        if add_style:
            ctx.write(reset, target=target)
        ctx.write("\n", target=target)
    if added_hidden:
        ctx.write(f"{ctx.palette.dim}… ({added_hidden} more lines){reset}\n", target=target)


def _render_image_placeholder(name, ctx, target):
    label = f" {name} "
    width = max(len(label) + 2, 20)
    ctx.write("┌" + "─" * width + "┐\n", target=target)
    ctx.write("│" + label.center(width) + "│\n", target=target)
    ctx.write("└" + "─" * width + "┘\n", target=target)


def _render_image_ansi(path, ctx, target):
    """24-bit ANSI half-block rendering via Pillow, scaled to a fixed
    terminal-friendly size (no real pane geometry is available through
    RenderContext today -- see docs/primitives.md's honest-limits section).
    Returns True on success; any failure (bad path, decode error, ...)
    returns False so the caller falls back to the placeholder box."""
    if Image is None:
        return False
    try:
        img = Image.open(path).convert("RGB")
    except (OSError, ValueError):
        return False
    max_w, max_h = 60, 60  # half-blocks double vertical resolution
    img.thumbnail((max_w, max_h))
    w, h = img.size
    reset = ctx.palette.reset or "\x1b[0m"
    for y in range(0, h, 2):
        chars = []
        for x in range(w):
            top = img.getpixel((x, y))
            bottom = img.getpixel((x, y + 1)) if y + 1 < h else top
            chars.append(
                f"\x1b[38;2;{top[0]};{top[1]};{top[2]}m"
                f"\x1b[48;2;{bottom[0]};{bottom[1]};{bottom[2]}m▀"
            )
        ctx.write("".join(chars) + reset + "\n", target=target)
    return True


def _perform_image(behavior, entry, ctx, target):
    field = behavior.get("field", "payload.image")
    ref = _resolve_field(entry, field)
    hold_s = max(float(behavior.get("hold_s", 0.0)), 0.0)
    if ref:
        name = Path(str(ref)).name  # basename only -- never trust path components
        rendered = False
        try:
            rendered = _render_image_ansi(str(ref), ctx, target)
        except Exception:
            log.debug("image render failed for %r; using placeholder", ref, exc_info=True)
        if not rendered:
            _render_image_placeholder(name, ctx, target)
    ctx.pacer.sleep(hold_s)


def _perform_behavior(behavior, entry, ctx, default_target):
    kind = behavior.get("behavior")
    target = behavior.get("target", default_target)

    if kind == "pause":
        ctx.pacer.sleep(max(float(behavior.get("seconds", 0.0)), 0.0))
        return
    if kind == "image":
        _perform_image(behavior, entry, ctx, target)
        return
    if "text" in behavior:
        _write_literal(behavior, entry, ctx, target)
        return

    content = _resolve_field(entry, behavior.get("field"))
    if content is None:
        return
    max_lines = behavior.get("max_lines", ctx.max_output_lines)

    if kind == "type":
        _perform_type(behavior, content, ctx, target, max_lines)
    elif kind == "print":
        _perform_print(behavior, content, ctx, target, max_lines)
    elif kind == "diff":
        _perform_diff(behavior, content, ctx, target, max_lines)
    else:
        log.debug("unknown behavior kind %r; nothing rendered", kind)


def _fire_avatar(ctx, avatar_cfg, entry):
    expression = avatar_cfg.get("expression", "idle")
    action = _format_literal(avatar_cfg.get("action", ""), entry)
    bubble_path = avatar_cfg.get("bubble")
    bubble = _resolve_field(entry, bubble_path) if bubble_path else None
    try:
        ctx.avatar(expression, action=action, bubble=bubble)
    except Exception:
        # RenderContext.avatar is documented never to raise; this is belt
        # and braces so a misbehaving avatar hook can never take the show
        # down (same spirit as Performer._avatar's own OSError guard).
        log.debug("avatar update raised; ignored", exc_info=True)


def _fire_on_error(ctx, on_error, entry, target):
    avatar_cfg = on_error.get("avatar")
    if avatar_cfg:
        _fire_avatar(ctx, avatar_cfg, entry)
    line_cfg = on_error.get("line")
    if line_cfg:
        text = _format_literal(line_cfg.get("text", ""), entry)
        style = _style_codes(ctx.palette, line_cfg.get("style"))
        reset = ctx.palette.reset
        ctx.write(f"{style}{text}{reset}\n" if style else f"{text}\n", target=target)


def perform_entry(entry, primitives, ctx):
    """Execute one render[] entry's behaviors against a RenderContext.

    Runs the recipe's `avatar` directive (if any) first, then every
    behavior in order, then its `on_error` directive when
    `payload.error` is truthy -- mirroring today's Performer handlers,
    which call the avatar hook near the top and check `event.get("error")`
    only after the handler itself has finished (app/replay.py's
    _on_tool_call). Neither avatar nor on_error contribute to
    estimate_entry_seconds -- errors don't cost extra screen time today,
    and this preserves that.
    """
    recipe = resolve_recipe(primitives, entry.get("primitive"))
    target = entry.get("target") or recipe.get("target") or "theater"

    avatar_cfg = recipe.get("avatar")
    if avatar_cfg:
        _fire_avatar(ctx, avatar_cfg, entry)

    for behavior in recipe.get("behaviors", []):
        _perform_behavior(behavior, entry, ctx, target)

    on_error = recipe.get("on_error")
    if on_error and _resolve_field(entry, "payload.error"):
        _fire_on_error(ctx, on_error, entry, target)
