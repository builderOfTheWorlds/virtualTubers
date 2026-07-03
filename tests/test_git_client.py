import pytest

from git_client import GitClient, GitError


@pytest.fixture
def repo(tmp_path):
    """A GitClient on an initialized repo containing one committed file."""
    (tmp_path / "hello.py").write_text("print('hello')\n", encoding="utf-8")
    git = GitClient(str(tmp_path), "TEST-1")
    git.init_repo()
    return git, tmp_path


def test_init_repo_creates_initial_commit(repo):
    git, _ = repo
    assert git.is_repo()
    assert git.head() is not None


def test_is_repo_false_outside_repo(tmp_path):
    git = GitClient(str(tmp_path / "empty"), "TEST-1")
    assert not git.is_repo()


def test_commit_all_returns_new_sha_on_changes(repo):
    git, path = repo
    before = git.head()
    (path / "new.py").write_text("x = 1\n", encoding="utf-8")

    sha = git.commit_all("feat: add new.py")

    assert sha is not None
    assert sha != before
    assert git.head() == sha


def test_commit_all_returns_none_when_clean(repo):
    git, _ = repo
    assert git.commit_all("feat: nothing") is None


def test_diff_stats_counts_changes(repo):
    git, path = repo
    before = git.head()
    (path / "new.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    git.commit_all("feat: add new.py")

    files, insertions, _ = git.diff_stats(before)

    assert files == 1
    assert insertions == 2


def test_diff_stats_zero_for_equal_or_missing_refs(repo):
    git, _ = repo
    head = git.head()
    assert git.diff_stats(head, head) == (0, 0, 0)
    assert git.diff_stats(None) == (0, 0, 0)


def test_push_without_remote_returns_false(repo):
    git, _ = repo
    git.remote_url = None
    assert git.push() is False


def test_commits_attributed_to_author(repo):
    git, _ = repo
    log = git._run("log", "-1", "--pretty=%an <%ae>")
    assert log == "TEST-1 <test-1@virtualtubers.local>"


def test_run_raises_giterror_with_stderr(tmp_path):
    git = GitClient(str(tmp_path), "TEST-1")
    with pytest.raises(GitError):
        git._run("rev-parse", "HEAD")  # not a repo
