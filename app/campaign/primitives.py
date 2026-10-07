"""Action primitives for the virtualTubers campaign runtime.

A "primitive" is the cosmetic verb a cast member performs in an action beat —
roll a check, cast a spell, run an exploit. It is PURELY NARRATION. There is no
dice engine, no RNG, no simulation, and no state. The script decides what
happens; a primitive only decides how it is worded. Rendering the same
primitive with the same arguments must produce byte-identical text forever, so
a recorded show replays word for word.

The registry is also what makes a second campaign config rather than code:
fantasy, cyberpunk, and office verbs are registered side by side, and each
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
    definition covers "Chadwick attacks the wraith." and "Chadwick attacks the
    wraith with his axe."
    """
    name: str
    genre: str                       # "fantasy", "cyber", or "office"
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


# ── cyber-police verbs (cyber_police) ────────────────────────────────────────
# A digital-crimes procedural vocabulary, alongside the existing cyber verbs
# above. Like every other primitive these only narrate: "raid" never breaches
# a door and "seize_evidence" never touches a real file. The script (or a
# live agent's already-completed action) supplies every result; the verb just
# words it for the stream.

CASE_PRIORITIES = ("low", "medium", "high", "critical")

DEFAULT_REGISTRY.register(Primitive(
    name="open_case",
    genre="cyber_police",
    summary="Open an investigation case file, optionally with a priority.",
    params=(
        ParamSpec("title", required=True),
        ParamSpec("priority", required=False, choices=CASE_PRIORITIES),
    ),
    template="{actor} opens a case file titled {title}",
    suffixes=(("priority", ", marked {priority} priority"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="assign_lead",
    genre="cyber_police",
    summary="Hand an investigative lead to a colleague, optionally with a due time.",
    params=(
        ParamSpec("to", required=True),
        ParamSpec("task", required=True),
        ParamSpec("due", required=False),
    ),
    template="{actor} assigns {task} to {to}",
    suffixes=(("due", ", due {due}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="trace_signal",
    genre="cyber_police",
    summary="Trace a signal or connection back toward its source.",
    params=(
        ParamSpec("target", required=True),
        ParamSpec("result", required=False),
    ),
    template="{actor} traces {target}",
    suffixes=(("result", " — the trail leads to {result}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="raid",
    genre="cyber_police",
    summary="Move in on a physical or digital location, optionally with backup.",
    params=(
        ParamSpec("location", required=True),
        ParamSpec("with_backup", required=False, choices=("yes", "no")),
    ),
    template="{actor} raids {location}",
    suffixes=(("with_backup", ", backup {with_backup}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="interrogate",
    genre="cyber_police",
    summary="Question a suspect, optionally about a specific subject.",
    params=(
        ParamSpec("subject_person", required=True),
        ParamSpec("about", required=False),
    ),
    template="{actor} interrogates {subject_person}",
    suffixes=(("about", " about {about}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="seize_evidence",
    genre="cyber_police",
    summary="Log a piece of evidence into custody, optionally from a location.",
    params=(
        ParamSpec("item", required=True),
        ParamSpec("from_location", required=False),
    ),
    template="{actor} seizes {item}",
    suffixes=(("from_location", " from {from_location}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="file_report",
    genre="cyber_police",
    summary="File a case report, optionally with a result.",
    params=(
        ParamSpec("title", required=True),
        ParamSpec("result", required=False),
    ),
    template="{actor} files a report: {title}",
    suffixes=(("result", " — {result}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="request_backup",
    genre="cyber_police",
    summary="Call for backup, optionally from a specific unit.",
    params=(
        ParamSpec("reason", required=True),
        ParamSpec("unit", required=False),
    ),
    template="{actor} calls for backup over {reason}",
    suffixes=(("unit", ", requesting {unit}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="brief_press",
    genre="cyber_police",
    summary="Brief the press or public on the unit's work, optionally to a named outlet.",
    params=(
        ParamSpec("topic", required=True),
        ParamSpec("outlet", required=False),
    ),
    template="{actor} briefs the press on {topic}",
    suffixes=(("outlet", " for {outlet}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="requisition",
    genre="cyber_police",
    summary="Requisition equipment or supplies, optionally with a justification.",
    params=(
        ParamSpec("item", required=True),
        ParamSpec("reason", required=False),
    ),
    template="{actor} requisitions {item}",
    suffixes=(("reason", ", for {reason}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="stand_watch",
    genre="cyber_police",
    summary="Watch silently, optionally watching something specific.",
    params=(
        ParamSpec("target", required=False),
    ),
    # The Observer's verb: it never speaks, so the line carries no speech.
    template="{actor} watches",
    suffixes=(("target", " {target}"),),
))


# ── office verbs (ashiorid_office) ───────────────────────────────────────────
# The Fraud-Stop workplace vocabulary. Like every other primitive these only
# narrate: "run_tests" never runs anything and "deploy" never ships anything.
# The script (or a live agent's already-completed action) supplies every
# result; the verb just words it for the stream.

OFFICE_SEVERITIES = ("low", "medium", "high", "critical")

DEFAULT_REGISTRY.register(Primitive(
    name="assign_task",
    genre="office",
    summary="Hand a task to a colleague, optionally with a due time.",
    params=(
        ParamSpec("to", required=True),
        ParamSpec("task", required=True),
        ParamSpec("due", required=False),
    ),
    template="{actor} assigns {task} to {to}",
    suffixes=(("due", ", due {due}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="write_spec",
    genre="office",
    summary="Write up a spec or requirements doc for a topic.",
    params=(
        ParamSpec("topic", required=True),
        ParamSpec("detail", required=False),
    ),
    template="{actor} writes up a spec for {topic}",
    suffixes=(("detail", ", covering {detail}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="open_ticket",
    genre="office",
    summary="Open a work ticket (an issue), optionally with a priority.",
    params=(
        ParamSpec("title", required=True),
        ParamSpec("priority", required=False, choices=OFFICE_SEVERITIES),
    ),
    template="{actor} opens a ticket titled {title}",
    suffixes=(("priority", ", marked {priority} priority"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="commit",
    genre="office",
    summary="Commit a change with a message, optionally to a named branch.",
    params=(
        ParamSpec("message", required=True),
        ParamSpec("branch", required=False),
    ),
    template='{actor} commits "{message}"',
    suffixes=(("branch", " to {branch}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="run_tests",
    genre="office",
    summary="Run a test suite; narrates a result only when the script supplies one.",
    params=(
        ParamSpec("suite", required=True),
        ParamSpec("result", required=False, choices=("pass", "fail")),
    ),
    template="{actor} runs {suite}",
    # As with roll_check, the verb never decides the result.
    suffixes=(("result", " — the result is {result}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="file_bug",
    genre="office",
    summary="File a bug report, optionally against a component and with a severity.",
    params=(
        ParamSpec("title", required=True),
        ParamSpec("component", required=False),
        ParamSpec("severity", required=False, choices=OFFICE_SEVERITIES),
    ),
    template="{actor} files a bug: {title}",
    suffixes=(("component", ", against {component}"),
              ("severity", ", severity {severity}")),
))

DEFAULT_REGISTRY.register(Primitive(
    name="open_pr",
    genre="office",
    summary="Open a pull request, optionally from a branch and for a reviewer.",
    params=(
        ParamSpec("title", required=True),
        ParamSpec("branch", required=False),
        ParamSpec("reviewer", required=False),
    ),
    template="{actor} opens a pull request for {title}",
    suffixes=(("branch", " from {branch}"),
              ("reviewer", ", asking {reviewer} to review")),
))

DEFAULT_REGISTRY.register(Primitive(
    name="merge_pr",
    genre="office",
    summary="Merge a pull request, optionally into a named branch.",
    params=(
        ParamSpec("pr", required=True),
        ParamSpec("into", required=False),
    ),
    template="{actor} merges {pr}",
    suffixes=(("into", " into {into}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="deploy",
    genre="office",
    summary="Ship a build to an environment; narrates an outcome only when scripted.",
    params=(
        ParamSpec("environment", required=True),
        ParamSpec("version", required=False),
        ParamSpec("outcome", required=False,
                  choices=("success", "failure", "rolled back")),
    ),
    template="{actor} deploys to {environment}",
    suffixes=(("version", ", shipping {version}"),
              ("outcome", " — {outcome}")),
))

DEFAULT_REGISTRY.register(Primitive(
    name="pitch",
    genre="office",
    summary="Pitch an idea, optionally to a named audience.",
    params=(
        ParamSpec("idea", required=True),
        ParamSpec("audience", required=False),
    ),
    template="{actor} pitches {idea}",
    suffixes=(("audience", " to {audience}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="brew_coffee",
    genre="office",
    summary="Brew a pot of coffee, optionally for someone and to a strength.",
    params=(
        ParamSpec("recipient", required=False),
        ParamSpec("strength", required=False),
    ),
    template="{actor} brews a fresh pot of coffee",
    suffixes=(("recipient", " for {recipient}"),
              ("strength", ", {strength}")),
))

DEFAULT_REGISTRY.register(Primitive(
    name="take_out_trash",
    genre="office",
    summary="Take out the trash, literal or digital (stale branches, old builds).",
    params=(
        ParamSpec("detail", required=False),
    ),
    template="{actor} takes out the trash",
    suffixes=(("detail", ": {detail}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="hr_notice",
    genre="office",
    summary="Post an HR notice about a subject, optionally to an audience.",
    params=(
        ParamSpec("subject", required=True),
        ParamSpec("audience", required=False),
    ),
    template="{actor} posts an HR notice about {subject}",
    suffixes=(("audience", " for {audience}"),),
))

DEFAULT_REGISTRY.register(Primitive(
    name="observe",
    genre="office",
    summary="Watch silently, optionally watching something specific.",
    params=(
        ParamSpec("target", required=False),
    ),
    # The Party Member's verb: it never speaks, so the line carries no speech.
    template="{actor} watches",
    suffixes=(("target", " {target}"),),
))
