# office.roles

## Overview

`app/office/roles.py` is the ashiorid_office roster as pure data: the 8 office
roles, the worker seat each occupies, the handler family each reuses, the chain
of command, and the Fraud-Stop repo lane each role may write to. It is the single
source of truth other office modules (protocol, configs, CODEOWNERS, live agents)
read from. No Kafka, LLM, or filesystem access.

Sources: `.claude/prompts/office_campaign_plan.md` §2 (roster, chain of command),
`.claude/prompts/ashiorid_office_build_plan.md` E2 (handler roles) and E3 (lanes).

| Role (`OfficeRole`) | Seat | `HANDLER_ROLE` (`agent.role`) | Reports to | Directs | Lane globs |
|---|---|---|---|---|---|
| `ceo` | tuber_0 | `ceo` | — | tech_lead, analyst, marketing, office_manager | none (issues only) |
| `tech_lead` | tuber_1 | `manager` | ceo | engineer, tester | `docs/design/**` |
| `analyst` | tuber_2 | `analyst` | ceo | — | `docs/requirements/**` |
| `engineer` | tuber_3 | `coder` | tech_lead | tester (test requests only) | `src/**` |
| `tester` | tuber_4 | `tester` | tech_lead | — | `tests/**` |
| `marketing` | tuber_5 | `marketing` | ceo | — | `marketing/**` |
| `office_manager` | tuber_6 | `office_manager` | ceo | — | `CHANGELOG.md`, `requirements*.txt`, `pyproject.toml` |
| `party_member` | tuber_7 | `observer` | — | — | none (read-only) |

## Signature

```python
class OfficeRole(str, Enum): ...          # CEO, TECH_LEAD, ANALYST, ENGINEER, TESTER,
                                          # MARKETING, OFFICE_MANAGER, PARTY_MEMBER
SEAT: dict[OfficeRole, str]
ROLE_AT_SEAT: dict[str, OfficeRole]
HANDLER_ROLE: dict[OfficeRole, str]
REPORTS_TO: dict[OfficeRole, OfficeRole | None]
DIRECTS: dict[OfficeRole, frozenset[OfficeRole]]
LANES: dict[OfficeRole, tuple[str, ...]]

def as_role(value: OfficeRole | str) -> OfficeRole
def role_for_seat(seat: str) -> OfficeRole | None
def can_direct(sender: OfficeRole | str, recipient: OfficeRole | str) -> bool
def superior_of(role: OfficeRole | str) -> OfficeRole | None
def normalize_repo_path(path: str) -> str | None
def lane_allows(role: OfficeRole | str, path: str) -> bool
```

## Parameters

- `value` / `sender` / `recipient` / `role` — an `OfficeRole`, a role value
  (`"tech_lead"`), or a seat id (`"tuber_1"`).
- `seat` — a worker id such as `"tuber_3"`.
- `path` — a **repo-relative** path in the Fraud-Stop repo. Backslashes and a leading
  `./` are normalized; absolute paths, drive letters and `..` segments are rejected.

Glob semantics for `LANES`: `**` matches across directories, `*` and `?` match within a
single path segment. Patterns are anchored at the repo root, so `requirements*.txt`
matches only root-level requirement files.

## Return Value

- `as_role` — the `OfficeRole`.
- `role_for_seat` — the role, or `None` for a non-office seat.
- `can_direct` — `True` only if `recipient in DIRECTS[sender]`; `False` for unknown roles.
- `superior_of` — the `REPORTS_TO` entry (`None` for CEO and Party Member).
- `normalize_repo_path` — normalized forward-slash path, or `None` if invalid.
- `lane_allows` — `True` if the path matches one of the role's lane globs.

## Dependencies

Standard library only: `enum`, `re`, `functools.lru_cache`, `logging`.

## Usage Examples

```python
from office.roles import OfficeRole, SEAT, HANDLER_ROLE, can_direct

SEAT[OfficeRole.ENGINEER]                        # "tuber_3"
HANDLER_ROLE[OfficeRole.TECH_LEAD]               # "manager"
can_direct("tuber_0", OfficeRole.ANALYST)        # True
can_direct(OfficeRole.PARTY_MEMBER, "ceo")       # False — directs nobody
```

```python
from office.roles import lane_allows

lane_allows("engineer", "src/fraud_stop/api.py")   # True
lane_allows("engineer", "tests/test_api.py")       # False — tester's lane
lane_allows("office_manager", "requirements-dev.txt")  # True
lane_allows("ceo", "CHANGELOG.md")                 # False — CEO works through issues
```

## Error Handling

- `as_role` / `superior_of` raise `ValueError` for anything that isn't a role or seat.
- `can_direct` and `lane_allows` never raise; unknown roles or invalid paths return `False`.

Logging: TRACE (level 5) on entry/exit, DEBUG on resolution/rejection decisions.

## Decisions

- The Party Member's `OfficeRole` value is `party_member` (matching cast ids); its handler
  family is `observer`.
- The Tester's single `REPORTS_TO` superior is the Tech Lead. The design lists
  "Tech Lead, Engineer"; the Engineer edge is modelled as `DIRECTS[ENGINEER] = {TESTER}`
  and used by the protocol only for `test_request`.
- The CEO's "reports to the Party Member, silently" is in-fiction only: `REPORTS_TO[CEO]`
  is `None`, and nobody directs the Party Member.

## Changelog

- **v1.0.0** (2026-09-27) — Initial version (OB-05): roles, seats, handler roles, chain of
  command, lanes.
