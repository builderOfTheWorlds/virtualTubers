import pytest

from git_client import GitClient
from test_runner import run_pytest, workspace_testable


def _make_workspace(tmp_path, test_body):
    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    (ws / "tests" / "test_thing.py").write_text(test_body, encoding="utf-8")
    GitClient(str(ws), "TESS-3").init_repo()
    return ws


def test_workspace_testable_requires_git_dir(tmp_path):
    ws = tmp_path / "ws"
    assert not workspace_testable(str(ws))
    ws.mkdir()
    assert not workspace_testable(str(ws))
    _make_workspace(tmp_path / "seeded", "def test_ok():\n    assert True\n")
    assert workspace_testable(str(tmp_path / "seeded" / "ws"))


@pytest.mark.slow
def test_run_pytest_passing_suite(tmp_path):
    ws = _make_workspace(tmp_path, "def test_ok():\n    assert True\n")

    result = run_pytest(str(ws))

    assert result.ran
    assert result.passed
    assert result.failed_tests == []


@pytest.mark.slow
def test_run_pytest_failing_suite_lists_failures(tmp_path):
    ws = _make_workspace(
        tmp_path,
        "def test_ok():\n    assert True\n\ndef test_broken():\n    assert False\n",
    )

    result = run_pytest(str(ws))

    assert result.ran
    assert not result.passed
    assert len(result.failed_tests) == 1
    assert "test_broken" in result.failed_tests[0]


@pytest.mark.slow
def test_run_pytest_empty_suite_produces_no_verdict(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("x = 1\n", encoding="utf-8")
    GitClient(str(ws), "TESS-3").init_repo()

    result = run_pytest(str(ws))

    # exit code 5 (no tests collected) must NOT count as a verdict
    assert not result.ran
    assert not result.passed
