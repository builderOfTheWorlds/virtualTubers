import pytest

from git_client import GitClient
from coding_backends.native_backend import NativeBackend, FILE_BLOCK_RE


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system_prompt, messages):
        self.calls.append((system_prompt, messages))
        if isinstance(self.responses[0], Exception):
            raise self.responses.pop(0)
        return self.responses.pop(0)


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    git = GitClient(str(ws), "NYX-1")
    git.init_repo()
    return ws, git


def _backend(workspace, git, llm):
    return NativeBackend(str(workspace), git, {}, 60, llm_client=llm)


def test_file_block_regex_parses_response():
    response = 'FILE: a.py\n```\nx = 1\n```\nFILE: dir/b.py\n```python\ny = 2\n```'
    blocks = FILE_BLOCK_RE.findall(response)
    assert blocks == [("a.py", "x = 1\n"), ("dir/b.py", "y = 2\n")]


def test_run_task_writes_files_and_commits(workspace):
    ws, git = workspace
    llm = FakeLLM(['FILE: calculator.py\n```\ndef add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n```'])
    before = git.head()

    result = _backend(ws, git, llm).run_task("add a sub function")

    assert result.success
    assert result.commit is not None and result.commit != before
    assert result.files_changed == 1
    assert "sub" in (ws / "calculator.py").read_text(encoding="utf-8")


def test_run_task_retries_once_on_unparseable_response(workspace):
    ws, git = workspace
    llm = FakeLLM([
        "sure, I'll do that!",  # no FILE blocks -> retry
        'FILE: new.py\n```\nz = 3\n```',
    ])

    result = _backend(ws, git, llm).run_task("add new.py")

    assert result.success
    assert len(llm.calls) == 2
    assert (ws / "new.py").exists()


def test_run_task_fails_after_two_unparseable_responses(workspace):
    ws, git = workspace
    llm = FakeLLM(["nope", "still nope"])

    result = _backend(ws, git, llm).run_task("do something")

    assert not result.success
    assert "no FILE blocks" in result.error
    assert result.commit is None


def test_run_task_rejects_path_escape(workspace):
    ws, git = workspace
    llm = FakeLLM([
        'FILE: ../evil.py\n```\nbad = True\n```',
        'FILE: ../evil.py\n```\nbad = True\n```',
    ])

    result = _backend(ws, git, llm).run_task("try an escape")

    assert not result.success
    assert "unsafe path" in result.error
    assert not (ws.parent / "evil.py").exists()


def test_run_task_llm_failure_returns_failed_result(workspace):
    ws, git = workspace
    llm = FakeLLM([RuntimeError("connection refused")])

    result = _backend(ws, git, llm).run_task("anything")

    assert not result.success
    assert "connection refused" in result.error
