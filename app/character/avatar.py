"""
character/avatar.py
Backstory -> avatar: turn a character profile's written `appearance` and
`personality` into the flat codec-head slider dict (app/character_schema.py)
by asking an LLM, then clamping/validating its answer through
`resolve_params` (OB-20, .claude/prompts/ashiorid_office_build_plan.md).

The LLM is anything with the app/llm_client.py interface:
`complete(system_prompt, messages) -> str`. Nothing here imports a provider,
so tests (and the offline `--params-file` path of
scripts/generate_office_avatars.py) pass a stand-in client.

Failure policy: one retry (with the previous error fed back to the model),
then the neutral PARAM_DEFAULTS, logged at ERROR. A bad answer never raises
out of `map_appearance` — a character with a default face is recoverable, a
generator run that dies on character 5 of 8 is not.
"""
import json
import logging
import re
import uuid
from dataclasses import dataclass, field

from character_schema import (
    ACCENT_COLORS,
    PARAM_DEFAULTS,
    SLIDER_DEFAULTS,
    CharacterParamError,
    resolve_params,
)

log = logging.getLogger(__name__)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")

#: Total attempts: the first call plus exactly one retry.
MAX_ATTEMPTS = 2

#: Every key the model must return — the 8 sliders plus accent_color.
REQUIRED_KEYS = tuple(SLIDER_DEFAULTS) + ("accent_color",)

#: What each slider means, in words the model can map a description onto.
#: Mirrors the table in docs/character_generator.md.
SLIDER_GUIDE = {
    "head_width": "0 = narrow skull, 1 = wide skull",
    "head_taper": "0 = blocky, straight-sided cranium, 1 = egg-shaped cranium narrowing above the brow",
    "eye_size": "0 = small / deep-set / heavy-lidded eyes, 1 = large, open eyes",
    "eye_spacing": "0 = close-set eyes, 1 = wide-set eyes",
    "jaw_width": "0 = narrow pointed chin, 1 = heavy, flared, square jaw",
    "nose_length": "0 = flat / short nose, 1 = long protruding nose",
    "ear_size": "0 = small flat ears, 1 = large flared ears",
    "build": "0 = thin neck / slight frame, 1 = thick neck / heavy frame",
}

SYSTEM_PROMPT = (
    "You turn a written character description into parameters for a low-poly 3D head. "
    "Reply with ONE JSON object and nothing else: no prose, no markdown fences. "
    "The object must have exactly these keys:\n"
    + "\n".join(f'- "{k}": a number from 0.0 to 1.0 ({v})' for k, v in SLIDER_GUIDE.items())
    + "\n- \"accent_color\": one of "
    + ", ".join(f'"{c}"' for c in ACCENT_COLORS)
    + " (a colour that suits the character's clothing or personality)\n"
    "Use the full 0..1 range: a strongly described feature belongs near 0 or 1, "
    "an undescribed one near 0.5. Face shape, build and age cues matter most."
)


@dataclass
class AvatarResult:
    """Outcome of one mapping: the params plus how they were obtained."""

    params: dict
    #: "llm" when a response validated, "default" when every attempt failed.
    source: str
    attempts: int
    errors: list = field(default_factory=list)


class AvatarResponseError(ValueError):
    """An LLM response that cannot be turned into character params."""


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


def build_user_prompt(profile):
    """Render the profile's identity, appearance and personality as the
    user message. Only those three blocks: the backstory is long and says
    nothing about a face."""
    _trace("op=build_user_prompt enter profile_id=%s", profile.get("id"))
    identity = profile.get("identity") or {}
    personality = profile.get("personality") or {}
    lines = []
    if identity.get("full_name"):
        lines.append(f"Name: {identity['full_name']}")
    if identity.get("age") is not None:
        lines.append(f"Age: {identity['age']}")
    if identity.get("pronouns"):
        lines.append(f"Pronouns: {identity['pronouns']}")
    lines.append("")
    lines.append("Appearance:")
    lines.append(str(profile.get("appearance") or "(not described)").strip())
    traits = personality.get("traits") or []
    if traits:
        lines.append("")
        lines.append("Personality traits:")
        lines.extend(f"- {t}" for t in traits)
    for key in ("work_style", "stress_response"):
        if personality.get(key):
            lines.append("")
            lines.append(f"{key.replace('_', ' ').capitalize()}: {personality[key]}")
    lines.append("")
    lines.append("Return the JSON object now.")
    prompt = "\n".join(lines)
    _trace("op=build_user_prompt exit chars=%d", len(prompt))
    return prompt


def _extract_json_object(text):
    """Pull the first {...} object out of a response. Tolerates markdown
    fences and a sentence of chatter around it, which small local models
    produce even when told not to."""
    if not isinstance(text, str):
        raise AvatarResponseError(f"response is not text: {type(text).__name__}")
    stripped = re.sub(r"```(?:json)?", "", text).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end < start:
        raise AvatarResponseError("no JSON object in response")
    try:
        data = json.loads(stripped[start:end + 1])
    except json.JSONDecodeError as exc:
        raise AvatarResponseError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AvatarResponseError("JSON is not an object")
    return data


def parse_response(text):
    """Turn one raw LLM response into a full, clamped params dict.

    Strict on structure (all 9 keys present, numeric sliders, a palette
    colour) because a missing key would silently become a default and the
    retry is the chance to get it right. Lenient on range (clamped by
    resolve_params) and on extra keys (dropped with a warning).
    """
    _trace("op=parse_response enter chars=%s", len(text) if isinstance(text, str) else None)
    data = _extract_json_object(text)
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise AvatarResponseError(f"missing keys: {missing}")
    extra = sorted(k for k in data if k not in PARAM_DEFAULTS)
    if extra:
        log.warning("op=parse_response dropped_keys=%s", extra)
    filtered = {k: data[k] for k in REQUIRED_KEYS}
    for key in SLIDER_DEFAULTS:
        value = filtered[key]
        # bool is an int subclass; "true" as a slider is a model error.
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise AvatarResponseError(f"slider {key!r} is not a number: {value!r}")
        try:
            number = float(value)
        except ValueError as exc:
            raise AvatarResponseError(f"slider {key!r} is not a number: {value!r}") from exc
        if not 0.0 <= number <= 1.0:
            log.debug("op=parse_response slider=%s raw=%s action=clamp", key, number)
    try:
        params = resolve_params(filtered, strict=True)
    except CharacterParamError as exc:
        raise AvatarResponseError(str(exc)) from exc
    # Round for readable YAML; 0.01 is far below what the renderer shows.
    params = {k: (round(v, 2) if k in SLIDER_DEFAULTS else v) for k, v in params.items()}
    _trace("op=parse_response exit params=%s", params)
    return params


def map_appearance_result(profile, llm_client, request_id=None):
    """Like map_appearance but also reports source/attempts/errors."""
    request_id = request_id or uuid.uuid4().hex[:8]
    profile_id = profile.get("id", "?")
    _trace("op=map_appearance enter request_id=%s profile_id=%s", request_id, profile_id)
    messages = [{"role": "user", "content": build_user_prompt(profile)}]
    errors = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        log.debug("op=llm_call request_id=%s profile_id=%s attempt=%d",
                  request_id, profile_id, attempt)
        try:
            text = llm_client.complete(SYSTEM_PROMPT, messages)
        except Exception as exc:  # any provider failure counts as a bad attempt
            log.error("op=llm_call request_id=%s profile_id=%s attempt=%d error=%s",
                      request_id, profile_id, attempt, exc)
            errors.append(f"llm call failed: {exc}")
            continue
        log.debug("op=llm_call request_id=%s profile_id=%s attempt=%d response_chars=%d",
                  request_id, profile_id, attempt, len(text) if isinstance(text, str) else -1)
        try:
            params = parse_response(text)
        except AvatarResponseError as exc:
            log.warning("op=parse_response request_id=%s profile_id=%s attempt=%d error=%s",
                        request_id, profile_id, attempt, exc)
            errors.append(str(exc))
            # Feed the error back so the retry can correct it rather than
            # repeat the same mistake.
            messages = messages + [
                {"role": "assistant", "content": text if isinstance(text, str) else str(text)},
                {"role": "user", "content": (
                    f"That was not usable ({exc}). Reply with only the JSON object "
                    f"with keys {list(REQUIRED_KEYS)}.")},
            ]
            continue
        log.info("op=map_appearance request_id=%s profile_id=%s source=llm attempts=%d",
                 request_id, profile_id, attempt)
        result = AvatarResult(params=params, source="llm", attempts=attempt, errors=errors)
        _trace("op=map_appearance exit request_id=%s result=%s", request_id, result)
        return result

    log.error("op=map_appearance request_id=%s profile_id=%s source=default "
              "attempts=%d errors=%s", request_id, profile_id, MAX_ATTEMPTS, errors)
    result = AvatarResult(params=dict(PARAM_DEFAULTS), source="default",
                          attempts=MAX_ATTEMPTS, errors=errors)
    _trace("op=map_appearance exit request_id=%s result=%s", request_id, result)
    return result


def map_appearance(profile, llm_client):
    """Map a profile's appearance + personality to codec-head params.

    Returns a full params dict (8 sliders + accent_color). Never raises for
    a bad LLM answer: retries once, then returns PARAM_DEFAULTS.
    """
    return map_appearance_result(profile, llm_client).params


class ReplayLLMClient:
    """An llm_client that replays canned responses instead of calling a model.

    Used by `generate_office_avatars.py --params-file` (no model reachable)
    and by tests. `responses` is one response or a list played in order; a
    dict/list-of-dicts is serialized to JSON text first so it goes through
    exactly the same parse path as a real reply. When the list runs out the
    last response repeats.
    """

    def __init__(self, responses):
        if not isinstance(responses, list):
            responses = [responses]
        if not responses:
            raise ValueError("ReplayLLMClient needs at least one response")
        self._responses = [r if isinstance(r, str) else json.dumps(r) for r in responses]
        self.calls = 0

    def complete(self, system_prompt, messages):
        index = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        _trace("op=replay_complete call=%d", self.calls)
        return self._responses[index]
