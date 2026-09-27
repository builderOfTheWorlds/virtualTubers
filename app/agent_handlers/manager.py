"""
agent_handlers/manager.py
Manager-side handlers (split out of app/agent.py): bug triage (re-delegate
or escalate, bounded by MAX_BUG_RETRIES), `test_passed` milestones,
narration-only `task_complete` acknowledgement, and `clarification_request`
blocker escalation. Also the opt-in task backlog's idle dispatch
(`BacklogDispatcher`, `manager_idle_tick` — docs/task_backlog.md): the
handlers above report chain activity / chain end to it.
"""
import time

from message_bus import build_message, correlation_of, reply_ids
from agent_state import write_state
from task_backlog import build_backlog

from .common import _complete_with_emotion, _send_manager_report


# Bug/fix loop bound: once an incoming bug_report's retry_count reaches this,
# the manager escalates to the operator instead of re-delegating.
MAX_BUG_RETRIES = 3


def handle_bug_report(worker_id, agent_config, llm_client, producer, msg,
                      state_path=None, coding_backend=None):
    """Manager triage: re-delegate a fix to the originating coder (payload
    coder_id — there are several coders now) with retry_count + 1, or — once
    the incoming retry_count reaches MAX_BUG_RETRIES, or the manager's own
    LLM fails — escalate to the operator instead (a blocker must not vanish
    because two LLM calls failed back to back).
    """
    role = agent_config.get("role")
    if role != "manager":
        print(f"[agent:{worker_id}] ignoring bug_report (role={role}, expected manager)")
        return

    payload = msg.get("payload", {})
    task = payload.get("task", "(no task description provided)")
    severity = payload.get("severity", "unknown")
    repro = payload.get("repro", "(no repro provided)")
    retry_count = payload.get("retry_count", 0)
    coder_id = payload.get("coder_id") or "coder"
    sender = msg.get("from") or "broadcast"
    at_cap = retry_count >= MAX_BUG_RETRIES
    # A bug report keeps the chain in flight (a re-delegated fix is a retry,
    # not a new task); only an escalation below ends it.
    _backlog_activity(msg)

    if state_path:
        write_state(state_path, "thinking", action=f"triaging bug: {task}")

    if at_cap:
        prompt = (
            f"{sender} reported a {severity} severity bug on '{task}' and it has already "
            f"been through {retry_count} fix attempts. Narrate escalating this blocker to "
            "the boss in 1-3 sentences, in character, as if speaking to the stream."
        )
    else:
        prompt = (
            f"{sender} reported a {severity} severity bug on '{task}'. Repro: {repro}\n\n"
            "Narrate reprioritizing and sending it back to the coder in 1-3 sentences, "
            "in character, as if speaking to the stream."
        )

    try:
        narration, emotion = _complete_with_emotion(
            llm_client, agent_config.get("system_prompt", ""), prompt)
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        narration, emotion = (
            f"(narration unavailable: {exc}) Escalating {severity} bug on '{task}' "
            f"after {retry_count} fix attempts."
        ), "neutral"
        if state_path:
            write_state(state_path, "frustrated", action=f"escalating: {task}", bubble=narration, emotion=emotion)
        _send_manager_report(
            worker_id, producer, "escalation", task, narration,
            extra={"severity": severity, "retry_count": retry_count},
            cause=msg,
        )
        _backlog_end(msg, "escalation")
        return

    print(f"[agent:{worker_id}] {narration}")
    if at_cap:
        if state_path:
            write_state(state_path, "frustrated", action=f"escalating: {task}", bubble=narration, emotion=emotion)
        _send_manager_report(
            worker_id, producer, "escalation", task, narration,
            extra={"severity": severity, "retry_count": retry_count},
            cause=msg,
        )
        _backlog_end(msg, "escalation")
        return

    if state_path:
        write_state(state_path, "speaking", action=f"re-delegated: {task}", bubble=narration, emotion=emotion)
    # The fix re-assignment stays on the bug_report's chain, which is the
    # original task's chain — so a task and all its retries share one
    # correlation_id.
    print(f"[agent:{worker_id}] re-delegating fix to={coder_id} retry_count={retry_count + 1} "
          f"correlation_id={correlation_of(msg)}")
    producer.send(build_message(
        worker_id, coder_id, "task_assignment",
        {
            "task": f"Fix bug ({severity}): {task}. Repro: {repro}",
            "retry_count": retry_count + 1,
        },
        **reply_ids(msg),
    ))


def handle_test_passed(worker_id, agent_config, llm_client, producer, msg,
                       state_path=None, coding_backend=None):
    role = agent_config.get("role")
    if role != "manager":
        print(f"[agent:{worker_id}] ignoring test_passed (role={role}, expected manager)")
        return

    payload = msg.get("payload", {})
    task = payload.get("task", "(no task description provided)")
    sender = msg.get("from") or "broadcast"
    prompt = (
        f"{sender} reports the full test suite passed for '{task}'!\n\n"
        "Narrate celebrating the team win in 1-3 sentences, in character, "
        "as if speaking to the stream."
    )

    # The suite passed: the chain is done whether or not the narration works.
    _backlog_end(msg, "milestone")

    if state_path:
        write_state(state_path, "thinking", action=f"reviewing results: {task}")

    try:
        narration, emotion = _complete_with_emotion(
            llm_client, agent_config.get("system_prompt", ""), prompt)
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        if state_path:
            write_state(state_path, "frustrated", action=f"failed: {task}", bubble=f"Ugh... {exc}")
        return

    print(f"[agent:{worker_id}] {narration}")
    if state_path:
        write_state(state_path, "happy", action=f"shipped: {task}", bubble=narration, emotion=emotion)
    _send_manager_report(worker_id, producer, "milestone", task, narration, cause=msg)


def handle_task_complete(worker_id, agent_config, llm_client, producer, msg,
                         state_path=None, coding_backend=None):
    """Manager acknowledges a coder's task_complete. Deliberately NO bus send:
    the coder's own commit_notification already drives the tester — sending
    anything here (e.g. a retest_request) would duplicate the test run. Do
    not "fix" this by adding a send.
    """
    role = agent_config.get("role")
    if role != "manager":
        print(f"[agent:{worker_id}] ignoring task_complete (role={role}, expected manager)")
        return


    payload = msg.get("payload", {})
    task = payload.get("task", "(no task description provided)")
    sender = msg.get("from") or "broadcast"
    prompt = (
        f"{sender} just finished the task '{task}' and handed the commit to the tester.\n\n"
        "Narrate acknowledging the progress in 1-3 sentences, in character, "
        "as if speaking to the stream."
    )

    _backlog_activity(msg)  # bookkeeping only — still NO bus send

    if state_path:
        write_state(state_path, "thinking", action=f"reviewing: {task}")

    try:
        narration, emotion = _complete_with_emotion(
            llm_client, agent_config.get("system_prompt", ""), prompt)
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        if state_path:
            write_state(state_path, "frustrated", action=f"failed: {task}", bubble=f"Ugh... {exc}")
        return

    print(f"[agent:{worker_id}] {narration}")
    if state_path:
        write_state(state_path, "speaking", action=f"acknowledged: {task}", bubble=narration, emotion=emotion)


def handle_clarification_request(worker_id, agent_config, llm_client, producer, msg,
                                 state_path=None, coding_backend=None):
    """Manager escalates a worker's blocker to the operator — ALWAYS sends a
    "blocker" manager_report (fallback narration if the manager's own LLM
    also fails). Deliberately does NOT auto-resend task_assignment: that
    risks a retry storm against a broken LLM endpoint (concept doc §11,
    "Human override").
    """
    role = agent_config.get("role")
    if role != "manager":
        print(f"[agent:{worker_id}] ignoring clarification_request (role={role}, expected manager)")
        return

    payload = msg.get("payload", {})
    task = payload.get("task", "(no task description provided)")
    error = payload.get("error", "(no error provided)")
    sender = msg.get("from") or "broadcast"
    prompt = (
        f"{sender} is blocked on '{task}' and needs help: {error}\n\n"
        "Narrate assessing the blocker and flagging it to the boss in 1-3 sentences, "
        "in character, as if speaking to the stream."
    )

    if state_path:
        write_state(state_path, "thinking", action=f"assessing blocker: {task}")

    try:
        narration, emotion = _complete_with_emotion(
            llm_client, agent_config.get("system_prompt", ""), prompt)
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        narration, emotion = (
            f"(narration unavailable: {exc}) {sender} is blocked on '{task}': {error}"
        ), "neutral"
    else:
        print(f"[agent:{worker_id}] {narration}")

    if state_path:
        write_state(state_path, "frustrated", action=f"blocker: {task}", bubble=narration, emotion=emotion)
    _send_manager_report(
        worker_id, producer, "blocker", task, narration,
        extra={"blocked_worker": sender, "error": error},
        cause=msg,
    )
    # A blocker ENDS the chain — the backlog item is marked "blocker" and is
    # never retried automatically (same no-auto-reassign rule as above).
    _backlog_end(msg, "blocker")


# ── Task backlog: idle dispatch (docs/task_backlog.md) ────────────────────────
DEFAULT_BACKLOG_COOLDOWN_S = 60
DEFAULT_BACKLOG_STALE_AFTER_S = 1800
DEFAULT_BACKLOG_CODERS = ("coder",)


class BacklogDispatcher:
    """Tracks the manager's in-flight task chains by correlation_id and,
    when none are in flight and the cooldown has passed, pulls the next
    backlog task and assigns it to a coder (round-robin over `coders`).

    A chain is in flight from dispatch until the manager's own
    milestone/escalation/blocker decision for it (or until it goes stale).
    Chains the backlog did not start (operator-posted tasks) are tracked too
    once the manager sees them (bug_report / task_complete), so the backlog
    never talks over an operator's task mid bug loop — ending one of those
    just never calls mark_done.

    `clock` is injectable (time.monotonic by default) for tests.
    """

    def __init__(self, source, coders=DEFAULT_BACKLOG_CODERS,
                 cooldown_s=DEFAULT_BACKLOG_COOLDOWN_S,
                 stale_after_s=DEFAULT_BACKLOG_STALE_AFTER_S, clock=None):
        self.source = source
        if isinstance(coders, str):
            coders = [coders]
        self.coders = [str(c) for c in (coders or DEFAULT_BACKLOG_CODERS)]
        self.cooldown_s = float(cooldown_s)
        self.stale_after_s = float(stale_after_s)
        self.clock = clock or time.monotonic
        self.inflight = {}
        self._next_coder = 0
        # The first pull also waits one cooldown after startup, so a
        # restarting manager doesn't fire a task before the team is up.
        self._next_poll_at = self.clock() + self.cooldown_s

    # -- chain bookkeeping (called from the manager handlers) --
    def note_activity(self, correlation_id):
        if not correlation_id:
            return
        now = self.clock()
        chain = self.inflight.get(correlation_id)
        if chain is None:
            self.inflight[correlation_id] = {"task_id": None, "title": None, "coder": None,
                                             "started_at": now, "last_activity": now}
            print(f"[agent:manager] backlog tracking foreign chain correlation_id={correlation_id}")
        else:
            chain["last_activity"] = now

    def end_chain(self, correlation_id, outcome):
        chain = self.inflight.pop(correlation_id, None) if correlation_id else None
        if chain is None:
            return None
        self._next_poll_at = self.clock() + self.cooldown_s
        print(f"[agent:manager] backlog chain ended correlation_id={correlation_id} "
              f"outcome={outcome} task_id={chain['task_id']}")
        if chain["task_id"] is not None:
            try:
                self.source.mark_done(chain["task_id"], outcome, correlation_id=correlation_id)
            except Exception as exc:
                print(f"[agent:manager] ERROR backlog mark_done failed task_id={chain['task_id']} "
                      f"outcome={outcome} error={exc}")
        return chain

    def drop_stale(self, producer=None, worker_id="manager"):
        """End every chain idle for more than stale_after_s as an
        "escalation" (WARN), telling the operator about backlog ones."""
        now = self.clock()
        stale = [cid for cid, c in self.inflight.items()
                 if now - c["last_activity"] > self.stale_after_s]
        for cid in stale:
            chain = self.inflight[cid]
            print(f"[agent:{worker_id}] WARN backlog dropping stale chain correlation_id={cid} "
                  f"task_id={chain['task_id']} idle_s={int(now - chain['last_activity'])}")
            self.end_chain(cid, "escalation")
            if producer is not None and chain["task_id"] is not None:
                _send_manager_report(
                    worker_id, producer, "escalation", chain["title"],
                    f"No word from the team on '{chain['title']}' for a while — "
                    "shelving it and flagging it for the boss.",
                    extra={"reason": "stale", "backlog_id": chain["task_id"]},
                    cause={"id": None, "correlation_id": cid},
                )
        return stale

    # -- dispatch --
    def ready(self):
        return not self.inflight and self.clock() >= self._next_poll_at

    def _pick_coder(self):
        coder = self.coders[self._next_coder % len(self.coders)]
        self._next_coder += 1
        return coder

    def tick(self, worker_id, agent_config, llm_client, producer, state_path=None):
        """One tick: drop stale chains, then dispatch if idle. Returns the
        sent task_assignment, or None."""
        self.drop_stale(producer, worker_id)
        if not self.ready():
            return None
        # Poll at most once per cooldown even when the backlog is empty, so
        # an empty Gitea label isn't hit every tick.
        self._next_poll_at = self.clock() + self.cooldown_s
        task = self.source.next_task()
        if not task:
            return None

        title = task.get("title") or task["id"]
        body = task.get("body") or ""
        coder = self._pick_coder()
        prompt = (
            f"The team is idle, so you're pulling the next ticket off the backlog: "
            f"'{title}'. You're assigning it to {coder}.\n\n"
            "Narrate kicking off this ticket in 1-3 sentences, in character, "
            "as if speaking to the stream."
        )
        if state_path:
            write_state(state_path, "thinking", action=f"picking next ticket: {title}")
        try:
            narration, emotion = _complete_with_emotion(
                llm_client, agent_config.get("system_prompt", ""), prompt)
        except Exception as exc:
            print(f"[agent:{worker_id}] LLM call failed: {exc}")
            narration, emotion = f"Next ticket off the backlog: '{title}' — over to {coder}.", "neutral"

        # No reply_ids: a backlog task STARTS a new correlation chain.
        msg = build_message(
            worker_id, coder, "task_assignment",
            {
                "task": f"{title}. {body}" if body else title,
                "retry_count": 0,
                "backlog_id": task["id"],
                "backlog_source": task.get("source"),
            },
        )
        producer.send(msg)
        correlation_id = correlation_of(msg)
        now = self.clock()
        self.inflight[correlation_id] = {"task_id": task["id"], "title": title, "coder": coder,
                                         "started_at": now, "last_activity": now}
        print(f"[agent:{worker_id}] {narration}")
        print(f"[agent:{worker_id}] backlog dispatched task_id={task['id']} to={coder} "
              f"source={task.get('source')} correlation_id={correlation_id}")
        if state_path:
            write_state(state_path, "speaking", action=f"assigned: {title}", bubble=narration, emotion=emotion)
        try:
            self.source.mark_started(task["id"], correlation_id=correlation_id)
        except Exception as exc:
            print(f"[agent:{worker_id}] ERROR backlog mark_started failed task_id={task['id']} error={exc}")
        return msg


# The one dispatcher of this process, built lazily from agent.backlog on the
# first tick (None = backlog disabled). Module-level because the handlers
# are stateless functions that need to report chain activity/end to it.
_backlog = None
_backlog_built = False


def _reset_backlog():
    """Forget this process's dispatcher (tests / config reload)."""
    global _backlog, _backlog_built
    _backlog, _backlog_built = None, False


def _get_backlog(agent_config):
    global _backlog, _backlog_built
    if not _backlog_built:
        _backlog_built = True
        cfg = agent_config.get("backlog") or {}
        source = build_backlog(cfg)
        if source is not None:
            _backlog = BacklogDispatcher(
                source,
                coders=cfg.get("coders") or DEFAULT_BACKLOG_CODERS,
                cooldown_s=cfg.get("cooldown_s", DEFAULT_BACKLOG_COOLDOWN_S),
                stale_after_s=cfg.get("stale_after_s", DEFAULT_BACKLOG_STALE_AFTER_S),
            )
            print(f"[agent:manager] backlog enabled source={source.name} coders={_backlog.coders} "
                  f"cooldown_s={_backlog.cooldown_s} stale_after_s={_backlog.stale_after_s}")
    return _backlog


def _backlog_activity(msg):
    if _backlog is not None:
        _backlog.note_activity(correlation_of(msg))


def _backlog_end(msg, outcome):
    if _backlog is not None:
        _backlog.end_chain(correlation_of(msg), outcome)


def manager_idle_tick(worker_id, agent_config, llm_client, producer, state_path=None):
    """Per-tick hook (agent_handlers.IDLE_TICK_HOOKS["manager"]) feeding the
    backlog dispatcher. No-op unless agent.backlog.enabled. Never raises: a
    backlog failure must not take the agent loop down."""
    if agent_config.get("role") != "manager":
        return None
    try:
        dispatcher = _get_backlog(agent_config)
        if dispatcher is None:
            return None
        return dispatcher.tick(worker_id, agent_config, llm_client, producer, state_path)
    except Exception as exc:
        print(f"[agent:{worker_id}] ERROR backlog idle tick failed: {exc}")
        return None
