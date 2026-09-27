"""
agent_handlers/coder.py
Coder-side handling of `task_assignment` (split out of app/agent.py): the
scripted tmux demos, the real coding-backend run, narration, and the
`task_complete` + `commit_notification` hand-off to the tester.
"""
from message_bus import build_message
from agent_state import write_state
from tmux_control import select_pane, send_keys, send_raw, send_command, TmuxError

from .common import _complete_with_emotion


def demo_editor_note(worker_id, task):
    """Scripted (non-LLM) demo of the agent acting on its own tmux UI (see
    docs/tmux_control.md): focus the editor pane and drop a fixed TODO
    comment noting the task, so pane-switching/typing is visible on stream
    ahead of any real LLM-driven tool use. nvim opens in normal mode, so "i"
    enters insert mode first and "Escape" returns to normal mode after —
    this only touches the in-memory buffer, it's never saved.

    Best-effort: no tmux session (e.g. running outside the container, or in
    tests) must not take the tick loop down, so tmux/pane-resolution
    failures are swallowed here rather than propagated.
    """
    flat_task = " ".join(task.split())
    try:
        select_pane("editor")
        send_raw("editor", "i")
        send_keys("editor", f"# TODO: {flat_task}")
        send_raw("editor", "Escape")
    except (TmuxError, OSError) as exc:
        print(f"[agent:{worker_id}] tmux editor demo skipped: {exc}")


def demo_filetree_ls(worker_id):
    """Scripted (non-LLM) demo of the agent using the filetree pane: focus
    it, run `ls` now that it's an interactive shell (see
    config/panels/filetree.yaml — no longer a `watch` loop, which can't
    accept keystrokes as commands), then refocus the editor pane so the
    coder visibly returns to work.

    Best-effort like demo_editor_note: no tmux session must not take the
    tick loop down.
    """
    try:
        select_pane("filetree")
        send_command("filetree", "ls")
        select_pane("editor")
    except (TmuxError, OSError) as exc:
        print(f"[agent:{worker_id}] tmux filetree demo skipped: {exc}")


def show_commit_in_filetree(worker_id, workspace):
    """After a real coding-backend run, show the resulting commit on stream:
    run `git show --stat HEAD` in the filetree pane (an interactive shell —
    see demo_filetree_ls) and refocus the editor. Best-effort: no tmux
    session must never fail the run itself."""
    try:
        select_pane("filetree")
        send_command("filetree", f"git -C {workspace} show --stat HEAD")
        select_pane("editor")
    except (TmuxError, OSError) as exc:
        print(f"[agent:{worker_id}] tmux commit replay skipped: {exc}")


def handle_task_assignment(worker_id, agent_config, llm_client, producer, msg,
                           state_path=None, coding_backend=None):
    payload = msg.get("payload", {})
    task = payload.get("task", "(no task description provided)")
    retry_count = payload.get("retry_count", 0)
    reply_to = msg.get("from") or "broadcast"

    if state_path:
        write_state(state_path, "thinking", action=f"working on: {task}")
    demo_editor_note(worker_id, task)
    demo_filetree_ls(worker_id)

    # Real work first (when this coder has a coding backend), narration
    # second — so the narration can describe what actually happened instead
    # of inventing an outcome.
    result = None
    if coding_backend is not None and agent_config.get("role") == "coder":
        if state_path:
            write_state(state_path, "focused", action=f"coding: {task}")
        result = coding_backend.run_task(task)
        print(
            f"[agent:{worker_id}] coding run backend={result.backend} "
            f"success={result.success} commit={result.commit} "
            f"files={result.files_changed} +{result.insertions}/-{result.deletions} "
            f"in {result.duration_s}s"
        )
        # Durable A/B record: message-logger unpacks this type into the
        # coding_backend_runs table (broadcast so the feed pane shows it too).
        producer.send(build_message(
            worker_id, "broadcast", "coding_run_report",
            {"task": task, "retry_count": retry_count, **result.to_payload()},
        ))
        if result.success:
            show_commit_in_filetree(worker_id, coding_backend.workspace)
        else:
            # A failed coding run is a blocker, not a commit to hand over —
            # same clarification_request contract the LLM-failure path uses,
            # so the manager escalates it identically.
            print(f"[agent:{worker_id}] coding run failed: {result.error}")
            if state_path:
                write_state(state_path, "frustrated", action=f"failed: {task}",
                            bubble=f"Ugh... {result.error}")
            producer.send(build_message(
                worker_id, "manager", "clarification_request",
                {"task": task, "error": f"coding backend failed: {result.error}"},
            ))
            return

    if result is not None:
        prompt = (
            f"You've just been assigned this task by {reply_to}: {task}\n\n"
            f"You implemented it for real: commit {result.commit[:8] if result.commit else '?'} "
            f"changed {result.files_changed} file(s) (+{result.insertions}/-{result.deletions}). "
            "Narrate what you did in 1-3 sentences, in character, as if speaking to the stream."
        )
    else:
        prompt = (
            f"You've just been assigned a new task by {reply_to}: {task}\n\n"
            "Narrate what you're doing in 1-3 sentences, in character, as if speaking to the stream."
        )

    try:
        narration, emotion = _complete_with_emotion(
            llm_client, agent_config.get("system_prompt", ""), prompt)
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        if state_path:
            write_state(state_path, "frustrated", action=f"failed: {task}", bubble=f"Ugh... {exc}")
        if result is not None and result.success:
            # The code exists and is committed; only the narration failed.
            # Hand the commit over anyway — never let a flaky narration LLM
            # strand real, finished work.
            narration, emotion = f"(narration unavailable: {exc})", "neutral"
        else:
            producer.send(build_message(
                worker_id, reply_to, "clarification_request",
                {"task": task, "error": str(exc)},
            ))
            return

    print(f"[agent:{worker_id}] {narration}")
    if state_path:
        write_state(state_path, "speaking", action=f"replied to {reply_to}", bubble=narration, emotion=emotion)
    producer.send(build_message(
        worker_id, reply_to, "task_complete",
        {"task": task, "narration": narration},
    ))

    # Coder hands the "commit" straight to the tester so the ticket keeps
    # flowing: coder -> tester -> manager. retry_count rides along so the
    # manager can bound the bug/fix loop (MAX_BUG_RETRIES); coder_id rides
    # along so the tester finds the right workspace mount and the manager
    # re-delegates fixes to the right coder now that there are several.
    if agent_config.get("role") == "coder":
        commit_payload = {
            "task": task,
            "commit_message": f"Implement: {task}",
            "narration": narration,
            "retry_count": retry_count,
            "coder_id": worker_id,
        }
        if result is not None:
            commit_payload.update({
                "commit": result.commit,
                "backend": result.backend,
                "files_changed": result.files_changed,
            })
        producer.send(build_message(
            worker_id, "tester", "commit_notification", commit_payload,
        ))
