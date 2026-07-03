"""
workspace_setup.py
Seeds a coder worker's workspace volume from the sandbox template baked into
the image (/app/sandbox), then git-inits it with an initial commit. Runs on
every coder startup and is idempotent: an already-seeded workspace (has a
.git) is left untouched so agent commits survive container restarts.
"""
import shutil
from pathlib import Path

from git_client import GitClient

DEFAULT_TEMPLATE = "/app/sandbox"


def ensure_workspace(workspace, author_name, template=DEFAULT_TEMPLATE):
    """Make `workspace` a ready git repo. Three cases:
    - already a git repo -> leave alone (idempotent restart)
    - missing/empty dir  -> copy template in, init + initial commit
    - files but no .git  -> init + initial commit of what's there (a
      pre-populated volume is treated as the intended starting tree)
    Returns the GitClient for the workspace.
    """
    ws = Path(workspace)
    git = GitClient(str(ws), author_name)

    if ws.is_dir() and (ws / ".git").exists():
        print(f"[workspace:{author_name}] {ws} already seeded (HEAD={git.head()})")
        return git

    ws.mkdir(parents=True, exist_ok=True)
    if not any(ws.iterdir()):
        src = Path(template)
        if not src.is_dir():
            raise FileNotFoundError(f"sandbox template not found at {src}")
        shutil.copytree(src, ws, dirs_exist_ok=True)
        print(f"[workspace:{author_name}] seeded {ws} from {src}")

    sha = git.init_repo()
    print(f"[workspace:{author_name}] initialized git repo at {ws} ({sha[:8] if sha else 'no commit'})")
    return git
