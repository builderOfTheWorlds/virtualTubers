import os
import shutil
import stat
import subprocess

import pytest

import git_client as git_client_mod
from git_client import GitClient, GitError, redact_url, remote_kind

FAKE_TOKEN = "tok_SECRET_1234567890abcdef"


@pytest.fixture
def repo(tmp_path):
    """A GitClient on an initialized repo containing one committed file."""
    (tmp_path / "hello.py").write_text("print('hello')\n", encoding="utf-8")
    git = GitClient(str(tmp_path), "TEST-1")
    git.init_repo()
    return git, tmp_path


@pytest.fixture
def no_remote_env(monkeypatch):
    for var in ("GIT_SERVER_URL", "GIT_SSH_COMMAND", "GIT_TOKEN_ENV", "GIT_HTTP_USER"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def bare_remote(tmp_path_factory):
    """A local bare repo acting as the remote."""
    path = tmp_path_factory.mktemp("remote") / "origin.git"
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True)
    return path


def _remote_refs(bare):
    out = subprocess.run(["git", "-C", str(bare), "show-ref"], capture_output=True, text=True)
    return out.stdout


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


# ── remote classification / redaction ────────────────────────────────────────

@pytest.mark.parametrize("url,kind", [
    (None, None),
    ("", None),
    ("http://192.168.1.120:3300/gitea_admin/fraud-stop.git", "http"),
    ("HTTPS://git.example/x.git", "http"),
    ("ssh://git@192.168.1.120:2222/gitea_admin/fraud-stop.git", "ssh"),
    ("git@192.168.1.120:gitea_admin/fraud-stop.git", "ssh"),
    ("/srv/git/fraud-stop.git", "local"),
    ("C:\\repos\\fraud-stop.git", "local"),
])
def test_remote_kind_classifies_urls(url, kind):
    assert remote_kind(url) == kind


def test_redact_url_strips_userinfo():
    assert redact_url("fatal: http://user:tok@h:3300/x.git") == "fatal: http://***@h:3300/x.git"
    assert redact_url("ssh://git@h:2222/x.git") == "ssh://git@h:2222/x.git"


def test_token_env_rejects_non_name_without_echoing_it(no_remote_env, tmp_path):
    with pytest.raises(ValueError) as exc:
        GitClient(str(tmp_path), "TEST-1", token_env="abc-123 not a name")
    assert "abc-123" not in str(exc.value)


# ── local-only defaults ──────────────────────────────────────────────────────

@pytest.mark.parametrize("op", ["push", "push_tag", "fetch", "push_branch"])
def test_remote_ops_noop_without_remote(repo, no_remote_env, monkeypatch, op):
    git, _ = repo
    assert git.remote_url is None
    calls = []
    monkeypatch.setattr(git, "_run_remote", lambda *a: calls.append(a))
    fn = getattr(git, op)
    result = fn("x") if op in ("push_tag", "push_branch") else fn()
    assert result is False
    assert calls == []


def test_empty_git_server_url_is_local_only(no_remote_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_SERVER_URL", "")
    assert GitClient(str(tmp_path), "TEST-1").remote_url is None


# ── real push/fetch/tag against a local bare remote ──────────────────────────

def test_push_branch_to_local_bare_remote(repo, no_remote_env, bare_remote):
    git, _ = repo
    git.remote_url = str(bare_remote)
    git.checkout_new_branch("feature/x")

    assert git.push_branch("feature/x") is True
    assert f"{git.head()} refs/heads/feature/x" in _remote_refs(bare_remote)


def test_push_repoints_existing_origin(repo, no_remote_env, bare_remote):
    git, _ = repo
    git._run("remote", "add", "origin", "/nonexistent/old.git")
    git.remote_url = str(bare_remote)
    assert git.push() is True
    assert git._run("remote", "get-url", "origin") == str(bare_remote)


def test_push_failure_returns_false_not_raises(repo, no_remote_env, tmp_path):
    git, _ = repo
    git.remote_url = str(tmp_path / "does-not-exist.git")
    assert git.push() is False


def test_create_and_push_tag_then_fetch(repo, no_remote_env, bare_remote, tmp_path_factory):
    git, _ = repo
    git.remote_url = str(bare_remote)
    sha = git.create_tag("loop-seed", message="seed")
    assert sha == git.head()
    assert git.push_tag("loop-seed") is True
    assert "refs/tags/loop-seed" in _remote_refs(bare_remote)

    clone_dir = tmp_path_factory.mktemp("clone") / "c"
    subprocess.run(["git", "clone", "-q", str(bare_remote), str(clone_dir)], check=True, capture_output=True)
    other = GitClient(str(clone_dir), "TEST-2", remote_url=str(bare_remote))
    assert other.fetch() is True
    assert other._run("rev-list", "-n", "1", "loop-seed") == sha


def test_create_tag_duplicate_raises(repo):
    git, _ = repo
    git.create_tag("v1")
    with pytest.raises(GitError):
        git.create_tag("v1")


def test_checkout_new_branch_from_tag_and_current_branch(repo):
    git, path = repo
    seed = git.create_tag("loop-seed")
    (path / "later.py").write_text("y = 2\n", encoding="utf-8")
    git.commit_all("feat: later")

    assert git.checkout_new_branch("loop/2026-W40", "loop-seed") == seed
    assert git.current_branch() == "loop/2026-W40"
    assert not (path / "later.py").exists()


def test_reset_hard_to_discards_commits_and_untracked(repo):
    git, path = repo
    seed = git.head()
    (path / "a.py").write_text("a = 1\n", encoding="utf-8")
    git.commit_all("feat: a")
    (path / "untracked.txt").write_text("junk", encoding="utf-8")
    (path / "hello.py").write_text("changed\n", encoding="utf-8")

    assert git.reset_hard_to(seed) == seed
    assert not (path / "a.py").exists()
    assert not (path / "untracked.txt").exists()
    assert not git.is_dirty()


# ── auth wiring (subprocess mocked) ──────────────────────────────────────────

class _Recorder:
    """Stands in for subprocess.run; snapshots argv, env and askpass file."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, cmd, capture_output=True, text=True, env=None):
        snap = {"cmd": list(cmd), "env": env, "askpass_content": None, "askpass_mode": None}
        askpass = (env or {}).get("GIT_ASKPASS")
        if askpass and os.path.exists(askpass):
            with open(askpass, encoding="utf-8") as fh:
                snap["askpass_content"] = fh.read()
            snap["askpass_mode"] = stat.S_IMODE(os.stat(askpass).st_mode)
        self.calls.append(snap)
        rc = 1 if self.fail_on and self.fail_on in cmd else 0
        stderr = f"fatal: unable to access 'http://gitea_admin:{FAKE_TOKEN}@h/x.git'" if rc else ""
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr=stderr)

    def network_calls(self):
        return [c for c in self.calls if "push" in c["cmd"] or "fetch" in c["cmd"]]


@pytest.fixture
def http_client(no_remote_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GITEA_TOKEN_OFFICE", FAKE_TOKEN)
    rec = _Recorder()
    monkeypatch.setattr(git_client_mod.subprocess, "run", rec)
    git = GitClient(str(tmp_path), "TEST-1",
                    remote_url="http://192.168.1.120:3300/gitea_admin/fraud-stop.git")
    return git, rec


def test_http_push_uses_askpass_and_never_exposes_token(http_client, caplog, capsys):
    git, rec = http_client
    caplog.set_level("DEBUG", logger="git_client")

    assert git.push_branch("feature/x") is True

    (call,) = rec.network_calls()
    assert FAKE_TOKEN not in " ".join(call["cmd"])
    assert "credential.helper=" in call["cmd"]
    assert call["cmd"][-2:] == ["origin", "feature/x"]
    env = call["env"]
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["VT_GIT_HTTP_USER"] == "gitea_admin"
    assert call["askpass_content"] is not None
    assert "${GITEA_TOKEN_OFFICE}" in call["askpass_content"]
    assert FAKE_TOKEN not in call["askpass_content"]
    if os.name == "posix":
        assert call["askpass_mode"] == 0o700
    assert not os.path.exists(env["GIT_ASKPASS"])  # cleaned up after the call
    for c in rec.calls:
        assert FAKE_TOKEN not in " ".join(c["cmd"])
    assert FAKE_TOKEN not in caplog.text
    assert FAKE_TOKEN not in capsys.readouterr().out


def test_http_push_custom_token_env(no_remote_env, monkeypatch, tmp_path):
    monkeypatch.setenv("MY_TOKEN", FAKE_TOKEN)
    rec = _Recorder()
    monkeypatch.setattr(git_client_mod.subprocess, "run", rec)
    git = GitClient(str(tmp_path), "TEST-1", remote_url="https://h/x.git", token_env="MY_TOKEN")
    assert git.push() is True
    assert "${MY_TOKEN}" in rec.network_calls()[0]["askpass_content"]


def test_http_push_failure_redacts_credentials(no_remote_env, monkeypatch, tmp_path, caplog, capsys):
    rec = _Recorder(fail_on="push")
    monkeypatch.setattr(git_client_mod.subprocess, "run", rec)
    git = GitClient(str(tmp_path), "TEST-1", remote_url="http://h/x.git")
    caplog.set_level("DEBUG", logger="git_client")

    assert git.push() is False
    assert FAKE_TOKEN not in caplog.text
    assert FAKE_TOKEN not in capsys.readouterr().out
    assert not os.path.exists(rec.network_calls()[0]["env"]["GIT_ASKPASS"])


def test_ssh_push_passes_ssh_command_and_no_askpass(no_remote_env, monkeypatch, tmp_path):
    monkeypatch.delenv("GIT_ASKPASS", raising=False)
    rec = _Recorder()
    monkeypatch.setattr(git_client_mod.subprocess, "run", rec)
    git = GitClient(str(tmp_path), "TEST-1",
                    remote_url="ssh://git@192.168.1.120:2222/gitea_admin/fraud-stop.git",
                    ssh_command="ssh -o StrictHostKeyChecking=accept-new")

    assert git.push("main", force=True) is True
    (call,) = rec.network_calls()
    assert call["env"]["GIT_SSH_COMMAND"] == "ssh -o StrictHostKeyChecking=accept-new"
    assert "GIT_ASKPASS" not in call["env"]
    assert "--force-with-lease" in call["cmd"]
    assert "credential.helper=" not in call["cmd"]


def test_ssh_command_defaults_from_env(no_remote_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -o StrictHostKeyChecking=accept-new")
    git = GitClient(str(tmp_path), "TEST-1", remote_url="git@h:o/r.git")
    assert git._remote_env()["GIT_SSH_COMMAND"] == "ssh -o StrictHostKeyChecking=accept-new"


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
def test_askpass_script_answers_username_and_token(no_remote_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GITEA_TOKEN_OFFICE", FAKE_TOKEN)
    git = GitClient(str(tmp_path), "TEST-1", remote_url="http://h/x.git")
    path = git._write_askpass()
    try:
        env = git._remote_env(path)
        user = subprocess.run(["sh", path, "Username for 'http://h': "], capture_output=True, text=True, env=env)
        pw = subprocess.run(["sh", path, "Password for 'http://gitea_admin@h': "],
                            capture_output=True, text=True, env=env)
    finally:
        os.unlink(path)
    assert user.stdout.strip() == "gitea_admin"
    assert pw.stdout.strip() == FAKE_TOKEN
