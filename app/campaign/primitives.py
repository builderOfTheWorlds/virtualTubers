"""Action primitives for the virtualTubers campaign runtime.

A "primitive" is the cosmetic verb a cast member performs in an action beat —
roll a check, cast a spell, run an exploit. It is PURELY NARRATION. There is no
dice engine, no RNG, no simulation, and no state. The script decides what
happens; a primitive only decides how it is worded. Rendering the same
primitive with the same arguments must produce byte-identical text forever, so
a recorded show replays word for word.

The registry is also what makes a second campaign config rather than code:
fantasy verbs and cyberpunk verbs are registered side by side, and each
campaign pack enables the subset it wants (pack.primitives is that list).
"""
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


class PrimitiveError(ValueError):
    """Raised for an unknown primitive, a duplicate registration, or a bad param."""
    pass


@dataclass(frozen=True)
class ParamSpec:
    """One argument a primitive accepts.

    An optional param that the script omits resolves to `default` (None unless
    stated), which is also the signal that suppresses its render suffix.
    """
    name: str
    required: bool = True
    default: Any = None       # used only when required is False
    choices: tuple = ()       # empty means "any value"


@dataclass(frozen=True)
class Primitive:
    """A cosmetic action verb: how a beat is worded, never what it does.

    `template` is formatted against the resolved params plus `actor` and always
    opens with the actor. Each entry in `suffixes` pairs a param name with a
    fragment appended only when the script supplied that param, so one
    definition covers "Buffalo attacks the wraith." and "Buffalo attacks the
    wraith with his axe."
    """
    name: str
    genre: str                       # "fantasy" or "cyber"
    summary: str                     # one line, for operator tooling
    params: tuple[ParamSpec, ...] = ()
    template: str = ""
    suffixes: tuple[tuple[str, str], ...] = ()

    def validate(self, params: dict | None = None) -> dict:
        """Return a new dict with every declared param resolved.

        Absent optional params are filled from their `default`. The supplied
        dict is never modified.

        Raises:
            PrimitiveError: a required param is missing, a supplied key matches
                no ParamSpec, or a value falls outside a declared `choices`.
        """
        if params is None:
            params = {}

        resolved = {}
        supplied = set(params.keys())

        for spec in self.params:
            if spec.name in supplied:
                value = params[spec.name]
                if value is not None and spec.choices and value not in spec.choices:
                    raise PrimitiveError(f"invalid value {value!r} for param {spec.name!r}")
                resolved[spec.name] = value
                supplied.remove(spec.name)
            elif spec.required:
                raise PrimitiveError(f"missing required param {spec.name!r}")
            else:
                resolved[spec.name] = spec.default

        if supplied:
            unknown = ", ".join(repr(name) for name in sorted(supplied))
            raise PrimitiveError(f"primitive {self.name!r} got unknown param(s): {unknown}")

        return resolved

    def render(self, actor: str, params: dict | None = None) -> str:
        """Narrate this primitive as one sentence. Raises only PrimitiveError."""
        resolved = self.validate(params)
        formatted = self.template.format(actor=actor, **resolved)

        for param_name, fragment in self.suffixes:
            if resolved[param_name] is not None:
                formatted += fragment.format(actor=actor, **resolved)

        if not formatted.endswith((".", "!", "?")):
            formatted += "."

        log.debug("rendered primitive %s for actor %s", self.name, actor)
        return formatted


class Registry:
    """Registry of action primitives for a campaign."""

    def __init__(self):
        self._primitives: dict[str, Primitive] = {}

    def register(self, primitive: Primitive) -> None:
        """Add a primitive. Raises PrimitiveError if the name is already taken."""
        if primitive.name in self._primitives:
            raise PrimitiveError(f"primitive {primitive.name!r} already registered")
        
        self._primitives[primitive.name] = primitive
        log.debug("registered primitive %s", primitive.name)

    def get(self, name: str) -> Primitive:
        """Look up a primitive. Raises PrimitiveError if it is not registered."""
        try:
            return self._primitives[name]
        except KeyError:
            raise PrimitiveError(f"unknown primitive {name!r}") from None

    def names(self, genre: str | None = None) -> list[str]:
        """Registered names, sorted; restricted to one genre when given."""
        return sorted(name for name, prim in self._primitives.items()
                      if genre is None or prim.genre == genre)

    def __contains__(self, name: str) -> bool:
        return name in self._primitives


# Module-level registry and convenience functions
DEFAULT_REGISTRY = Registry()


def get(name: str) -> Primitive:
    """Look up a shipped primitive by name."""
    return DEFAULT_REGISTRY.get(name)


def names(genre: str | None = None) -> list[str]:
    """Shipped primitive names, sorted; restricted to one genre when given."""
    return DEFAULT_REGISTRY.names(genre)


def render(name: str, actor: str, params: dict | None = None) -> str:
    """Narrate a shipped primitive — the call an action beat makes."""
    return DEFAULT_REGISTRY.get(name).render(actor, params)


# ── the shipped vocabulary ───────────────────────────────────────────────────
# These strings are spoken aloud on stream, so they are written to read as
# narration rather than as a log line. Each template opens with the actor and
# carries no trailing punctuation; render() closes the sentence.
#
# No template puts an article in front of a param — "a {skill}" mispronounces
# every skill starting with a vowel. Where a value needs one, the script writes
# it into the value ("the wraith", "his greataxe").

DEFAULT_REGISTRY.register(Primitive(
    name="roll_check",
    genre="fantasy",
    summary="Roll a skill check against a difficulty the script sets.",
    params=(
        ParamSpec("skill", required=True),
        ParamSpec("dc", required=False),
        ParamSpec("outcome", required=False, choices=("success", "failure")),
    ),
    template="{actor} rolls {skill}",
    # The roll never decides anything: an outcome is narrated only when the
    # script already supplied one.
    suffixes=(("dc", " against DC {dc}"), ("outcome", " — a {outcome}")),
))

DEFAULT_REGISTRY.register(Primitive(
    name="cast_spell",
    genre="fantasy",
    summary="Cast a named spell, optionally at a level and a target.",
    params=(
        ParamSpec("spell", required=True),
        ParamSpec("target", required=False),
        ParamSpec("level", required=False),
    ),
    template="{actor} casts {spell}",
    suffixes=(("level", " at level {level}"), ("target", " on {target}")),
))

DEFAULT_REGISTRY.register(Primitive(
    name="attack",
    genre="fantasy",
    summary="Strike a target, optionally with a named weapon.",
    params=(
        ParamSpec("target", required=True),
        ParamSpec("weapon", required=False),
    ),
    template="{actor} attacks {target}",
    suffixes=(("weapon", " with {weapon}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="move_to",
    genre="fantasy",
    summary="Cross to somewhere else in the scene.",
    params=(
        ParamSpec("destination", required=True),
        ParamSpec("manner", required=False),
    ),
    template="{actor} moves to {destination}",
    suffixes=(("manner", ", {manner}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="search",
    genre="fantasy",
    summary="Search a place or object, optionally for something specific.",
    params=(
        ParamSpec("target", required=True),
        ParamSpec("detail", required=False),
    ),
    template="{actor} searches {target}",
    suffixes=(("detail", " for {detail}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="reveal_memory",
    genre="fantasy",
    summary="Surface a memory carried over from a previous run of the loop.",
    params=(
        ParamSpec("subject", required=True),
        ParamSpec("detail", required=False),
    ),
    # The loop-carrying character does not remember by choosing to; the memory
    # arrives. Worded as a vision so it reads apart from ordinary action.
    template="{actor} is struck by a memory of {subject}",
    suffixes=(("detail", " — {detail}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="execute_exploit",
    genre="cyber",
    summary="Run a named exploit, optionally against a target.",
    params=(
        ParamSpec("exploit", required=True),
        ParamSpec("target", required=False),
    ),
    template="{actor} fires off {exploit}",
    suffixes=(("target", " against {target}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="scan_target",
    genre="cyber",
    summary="Sweep a target for information.",
    params=(
        ParamSpec("target", required=True),
        ParamSpec("depth", required=False),
    ),
    template="{actor} scans {target}",
    suffixes=(("depth", " down to {depth}"),),
))
