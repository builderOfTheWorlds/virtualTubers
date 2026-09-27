"""
office/roles.py
The ashiorid_office roster as data: the 8 office roles, their seats
(worker ids), the handler family each reuses, the chain of command, and the
Fraud-Stop repo lane each role may write to.

Pure module: no Kafka, no LLM, no filesystem. Sources of truth:
  - .claude/prompts/office_campaign_plan.md §2 (roster, chain of command)
  - .claude/prompts/ashiorid_office_build_plan.md E2 (handler roles), E3 (lanes)
See docs/office_roles.md.
"""
import logging
import re
from enum import Enum
from functools import lru_cache

log = logging.getLogger(__name__)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def _trace(msg, *args):
    if log.isEnabledFor(TRACE):
        log.log(TRACE, msg, *args)


class OfficeRole(str, Enum):
    """The 8 office roles. Values double as `agent.office_role` config values."""
    CEO = "ceo"
    TECH_LEAD = "tech_lead"
    ANALYST = "analyst"
    ENGINEER = "engineer"
    TESTER = "tester"
    MARKETING = "marketing"
    OFFICE_MANAGER = "office_manager"
    PARTY_MEMBER = "party_member"


#: role -> worker id (design §2 slot column).
SEAT = {
    OfficeRole.CEO: "tuber_0",
    OfficeRole.TECH_LEAD: "tuber_1",
    OfficeRole.ANALYST: "tuber_2",
    OfficeRole.ENGINEER: "tuber_3",
    OfficeRole.TESTER: "tuber_4",
    OfficeRole.MARKETING: "tuber_5",
    OfficeRole.OFFICE_MANAGER: "tuber_6",
    OfficeRole.PARTY_MEMBER: "tuber_7",
}

#: worker id -> role (inverse of SEAT).
ROLE_AT_SEAT = {seat: role for role, seat in SEAT.items()}

#: role -> `agent.role` handler family (build plan E2). Tech Lead / Engineer /
#: Tester reuse the proven manager -> coder -> tester loop; the rest get new
#: handler families. The Party Member's handler family is "observer".
HANDLER_ROLE = {
    OfficeRole.CEO: "ceo",
    OfficeRole.TECH_LEAD: "manager",
    OfficeRole.ANALYST: "analyst",
    OfficeRole.ENGINEER: "coder",
    OfficeRole.TESTER: "tester",
    OfficeRole.MARKETING: "marketing",
    OfficeRole.OFFICE_MANAGER: "office_manager",
    OfficeRole.PARTY_MEMBER: "observer",
}

#: role -> the single superior a status_report goes to (None = reports to
#: nobody). The Tester's formal superior is the Tech Lead; the Engineer's
#: test requests are a lateral channel, not a reporting line. The CEO
#: "reports" to the Party Member only silently, in fiction — not on the bus.
REPORTS_TO = {
    OfficeRole.CEO: None,
    OfficeRole.TECH_LEAD: OfficeRole.CEO,
    OfficeRole.ANALYST: OfficeRole.CEO,
    OfficeRole.ENGINEER: OfficeRole.TECH_LEAD,
    OfficeRole.TESTER: OfficeRole.TECH_LEAD,
    OfficeRole.MARKETING: OfficeRole.CEO,
    OfficeRole.OFFICE_MANAGER: OfficeRole.CEO,
    OfficeRole.PARTY_MEMBER: None,
}

#: role -> roles it may give work to (directive / technical tasking).
#: The Engineer -> Tester edge covers test_request only (design §2
#: "Directs: Tester (test requests)").
DIRECTS = {
    OfficeRole.CEO: frozenset({OfficeRole.TECH_LEAD, OfficeRole.ANALYST,
                               OfficeRole.MARKETING, OfficeRole.OFFICE_MANAGER}),
    OfficeRole.TECH_LEAD: frozenset({OfficeRole.ENGINEER, OfficeRole.TESTER}),
    OfficeRole.ANALYST: frozenset(),
    OfficeRole.ENGINEER: frozenset({OfficeRole.TESTER}),
    OfficeRole.TESTER: frozenset(),
    OfficeRole.MARKETING: frozenset(),
    OfficeRole.OFFICE_MANAGER: frozenset(),
    OfficeRole.PARTY_MEMBER: frozenset(),
}

#: role -> Fraud-Stop repo path globs the role may write (build plan E3).
#: `**` matches across directories, `*` / `?` stay within one segment.
#: CEO works through issues only; the Party Member never writes.
LANES = {
    OfficeRole.CEO: (),
    OfficeRole.TECH_LEAD: ("docs/design/**",),
    OfficeRole.ANALYST: ("docs/requirements/**",),
    OfficeRole.ENGINEER: ("src/**",),
    OfficeRole.TESTER: ("tests/**",),
    OfficeRole.MARKETING: ("marketing/**",),
    OfficeRole.OFFICE_MANAGER: ("CHANGELOG.md", "requirements*.txt", "pyproject.toml"),
    OfficeRole.PARTY_MEMBER: (),
}


def as_role(value):
    """Coerce an OfficeRole, a role value ('tech_lead') or a seat ('tuber_1')
    to an OfficeRole. Raises ValueError for anything else."""
    _trace("as_role enter value=%r", value)
    if isinstance(value, OfficeRole):
        return value
    if isinstance(value, str):
        if value in ROLE_AT_SEAT:
            log.debug("as_role resolved seat seat=%s role=%s", value, ROLE_AT_SEAT[value].value)
            return ROLE_AT_SEAT[value]
        try:
            return OfficeRole(value)
        except ValueError:
            pass
    log.debug("as_role rejected value=%r", value)
    raise ValueError(f"not an office role or seat: {value!r}")


def role_for_seat(seat):
    """The OfficeRole seated at worker id `seat`, or None if the seat is not an office seat."""
    role = ROLE_AT_SEAT.get(seat)
    _trace("role_for_seat seat=%r role=%s", seat, role.value if role else None)
    return role


def can_direct(sender, recipient):
    """True when `sender` may give work to `recipient` (roles or seats)."""
    _trace("can_direct enter sender=%r recipient=%r", sender, recipient)
    try:
        s, r = as_role(sender), as_role(recipient)
    except ValueError:
        log.debug("can_direct unknown role sender=%r recipient=%r", sender, recipient)
        return False
    allowed = r in DIRECTS[s]
    _trace("can_direct exit sender=%s recipient=%s allowed=%s", s.value, r.value, allowed)
    return allowed


def superior_of(role):
    """The role `role` reports to, or None."""
    return REPORTS_TO[as_role(role)]


@lru_cache(maxsize=None)
def _glob_regex(pattern):
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def normalize_repo_path(path):
    """Normalize a repo-relative path to forward-slash form without a leading
    './'. Returns None for paths that escape or aren't repo-relative
    (absolute, drive-letter, or containing a '..' segment) or are empty."""
    if not isinstance(path, str):
        return None
    p = path.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    if not p or p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        return None
    parts = [seg for seg in p.split("/") if seg not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def lane_allows(role, path):
    """True when `role` may write repo-relative `path` per LANES."""
    _trace("lane_allows enter role=%r path=%r", role, path)
    try:
        r = as_role(role)
    except ValueError:
        log.debug("lane_allows unknown role role=%r", role)
        return False
    norm = normalize_repo_path(path)
    if norm is None:
        log.debug("lane_allows rejected path role=%s path=%r", r.value, path)
        return False
    allowed = any(_glob_regex(g).match(norm) for g in LANES[r])
    _trace("lane_allows exit role=%s path=%s allowed=%s", r.value, norm, allowed)
    return allowed
