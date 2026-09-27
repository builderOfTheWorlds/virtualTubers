"""Tests for app/office/roles.py (OB-05)."""
import pytest

from office.roles import (
    DIRECTS,
    HANDLER_ROLE,
    LANES,
    REPORTS_TO,
    ROLE_AT_SEAT,
    SEAT,
    OfficeRole,
    as_role,
    can_direct,
    lane_allows,
    normalize_repo_path,
    role_for_seat,
    superior_of,
)

R = OfficeRole
ALL_TABLES = [SEAT, HANDLER_ROLE, REPORTS_TO, DIRECTS, LANES]


@pytest.mark.parametrize("table", ALL_TABLES)
def test_tables_cover_every_role(table):
    assert set(table) == set(OfficeRole)


def test_office_role_has_eight_roles():
    assert len(OfficeRole) == 8


@pytest.mark.parametrize("role,seat", [
    (R.CEO, "tuber_0"), (R.TECH_LEAD, "tuber_1"), (R.ANALYST, "tuber_2"),
    (R.ENGINEER, "tuber_3"), (R.TESTER, "tuber_4"), (R.MARKETING, "tuber_5"),
    (R.OFFICE_MANAGER, "tuber_6"), (R.PARTY_MEMBER, "tuber_7"),
])
def test_seat_matches_design_roster(role, seat):
    assert SEAT[role] == seat
    assert ROLE_AT_SEAT[seat] is role
    assert role_for_seat(seat) is role


@pytest.mark.parametrize("role,handler", [
    (R.CEO, "ceo"), (R.TECH_LEAD, "manager"), (R.ANALYST, "analyst"),
    (R.ENGINEER, "coder"), (R.TESTER, "tester"), (R.MARKETING, "marketing"),
    (R.OFFICE_MANAGER, "office_manager"), (R.PARTY_MEMBER, "observer"),
])
def test_handler_role_follows_e2(role, handler):
    assert HANDLER_ROLE[role] == handler


@pytest.mark.parametrize("value,expected", [
    (R.ANALYST, R.ANALYST), ("analyst", R.ANALYST), ("tuber_1", R.TECH_LEAD),
    ("party_member", R.PARTY_MEMBER),
])
def test_as_role_accepts_role_value_or_seat(value, expected):
    assert as_role(value) is expected


@pytest.mark.parametrize("value", ["tuber_8", "manager", "", None, 3])
def test_as_role_rejects_unknown(value):
    with pytest.raises(ValueError):
        as_role(value)


def test_role_for_seat_unknown_is_none():
    assert role_for_seat("tuber_9") is None


@pytest.mark.parametrize("sender,recipient,expected", [
    (R.CEO, R.TECH_LEAD, True), (R.CEO, R.ANALYST, True), (R.CEO, R.MARKETING, True),
    (R.CEO, R.OFFICE_MANAGER, True), (R.CEO, R.ENGINEER, False), (R.CEO, R.TESTER, False),
    (R.TECH_LEAD, R.ENGINEER, True), (R.TECH_LEAD, R.TESTER, True),
    (R.TECH_LEAD, R.CEO, False), (R.TECH_LEAD, R.ANALYST, False),
    (R.ENGINEER, R.TESTER, True), (R.ENGINEER, R.TECH_LEAD, False),
    (R.TESTER, R.ENGINEER, False), (R.ANALYST, R.TECH_LEAD, False),
    (R.MARKETING, R.ENGINEER, False), (R.OFFICE_MANAGER, R.CEO, False),
    ("tuber_0", "tuber_1", True), ("tuber_1", "tuber_0", False),
    ("nobody", R.CEO, False), (R.CEO, "nobody", False),
])
def test_can_direct_follows_chain_of_command(sender, recipient, expected):
    assert can_direct(sender, recipient) is expected


@pytest.mark.parametrize("other", list(OfficeRole))
def test_party_member_directs_nobody_and_nobody_directs_them(other):
    assert can_direct(R.PARTY_MEMBER, other) is False
    assert can_direct(other, R.PARTY_MEMBER) is False


@pytest.mark.parametrize("role,superior", [
    (R.CEO, None), (R.TECH_LEAD, R.CEO), (R.ANALYST, R.CEO), (R.ENGINEER, R.TECH_LEAD),
    (R.TESTER, R.TECH_LEAD), (R.MARKETING, R.CEO), (R.OFFICE_MANAGER, R.CEO),
    (R.PARTY_MEMBER, None),
])
def test_superior_of(role, superior):
    assert superior_of(role) is superior


def test_directs_and_reports_to_are_consistent():
    # Everyone a role reports to directs it (the Engineer->Tester edge is lateral).
    for role, sup in REPORTS_TO.items():
        if sup is not None:
            assert role in DIRECTS[sup]


@pytest.mark.parametrize("role,path,expected", [
    (R.ENGINEER, "src/fraud_stop/api.py", True),
    (R.ENGINEER, "src/x.py", True),
    (R.ENGINEER, "tests/test_api.py", False),
    (R.ENGINEER, "srcx/a.py", False),
    (R.TESTER, "tests/unit/test_rules.py", True),
    (R.TESTER, "src/a.py", False),
    (R.ANALYST, "docs/requirements/fr-001.md", True),
    (R.ANALYST, "docs/design/a.md", False),
    (R.TECH_LEAD, "docs/design/arch.md", True),
    (R.TECH_LEAD, "src/a.py", False),
    (R.MARKETING, "marketing/landing.md", True),
    (R.MARKETING, "README.md", False),
    (R.OFFICE_MANAGER, "CHANGELOG.md", True),
    (R.OFFICE_MANAGER, "requirements.txt", True),
    (R.OFFICE_MANAGER, "requirements-dev.txt", True),
    (R.OFFICE_MANAGER, "pyproject.toml", True),
    (R.OFFICE_MANAGER, "sub/requirements.txt", False),
    (R.OFFICE_MANAGER, "src/a.py", False),
    (R.CEO, "CHANGELOG.md", False),
    (R.CEO, "src/a.py", False),
    (R.PARTY_MEMBER, "src/a.py", False),
    (R.PARTY_MEMBER, "README.md", False),
    ("tuber_3", "src/a.py", True),
    (R.ENGINEER, "./src/a.py", True),
    (R.ENGINEER, "src\\pkg\\a.py", True),
    (R.ENGINEER, "src/../tests/a.py", False),
    (R.ENGINEER, "/src/a.py", False),
    (R.ENGINEER, "C:/repo/src/a.py", False),
    (R.ENGINEER, "", False),
    (R.ENGINEER, None, False),
    ("nobody", "src/a.py", False),
])
def test_lane_allows(role, path, expected):
    assert lane_allows(role, path) is expected


def test_lanes_do_not_overlap():
    sample = ["src/a.py", "tests/t.py", "docs/requirements/r.md", "docs/design/d.md",
              "marketing/m.md", "CHANGELOG.md", "requirements.txt", "pyproject.toml"]
    for path in sample:
        owners = [r for r in OfficeRole if lane_allows(r, path)]
        assert len(owners) == 1, (path, owners)


@pytest.mark.parametrize("path,expected", [
    ("./a/b.py", "a/b.py"), ("a//b.py", "a/b.py"), ("a\\b.py", "a/b.py"),
    ("../a", None), ("/a", None), ("", None), (".", None),
])
def test_normalize_repo_path(path, expected):
    assert normalize_repo_path(path) == expected
