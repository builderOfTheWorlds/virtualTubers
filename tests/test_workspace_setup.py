import pytest

from workspace_setup import ensure_workspace


@pytest.fixture
def template(tmp_path):
    src = tmp_path / "template"
    src.mkdir()
    (src / "app.py").write_text("x = 1\n", encoding="utf-8")
    return src


def test_ensure_workspace_seeds_empty_dir_from_template(template, tmp_path):
    ws = tmp_path / "workspace"

    git = ensure_workspace(str(ws), "TEST-1", template=str(template))

    assert (ws / "app.py").exists()
    assert (ws / ".git").exists()
    assert git.head() is not None


def test_ensure_workspace_idempotent_on_seeded_workspace(template, tmp_path):
    ws = tmp_path / "workspace"
    first = ensure_workspace(str(ws), "TEST-1", template=str(template))
    head = first.head()
    (ws / "agent_work.py").write_text("y = 2\n", encoding="utf-8")
    first.commit_all("feat: agent work")

    second = ensure_workspace(str(ws), "TEST-1", template=str(template))

    # Restart must not reseed or lose agent commits.
    assert second.head() == first.head()
    assert second.head() != head


def test_ensure_workspace_inits_prepopulated_dir_without_template_copy(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "existing.py").write_text("z = 3\n", encoding="utf-8")

    git = ensure_workspace(str(ws), "TEST-1", template=str(tmp_path / "nonexistent"))

    assert git.head() is not None
    assert (ws / "existing.py").exists()


def test_ensure_workspace_missing_template_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ensure_workspace(str(tmp_path / "ws"), "TEST-1", template=str(tmp_path / "nope"))
