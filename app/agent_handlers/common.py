"""
agent_handlers/common.py
Helpers shared by several message handlers (split out of app/agent.py):
the structured LLM narration call every narrating handler uses, and the
manager -> operator report sender.
"""
from message_bus import build_message
from emotion import STRUCTURED_REPLY_INSTRUCTION, parse_structured_reply


def _complete_with_emotion(llm_client, system_prompt, prompt):
    """llm_client.complete(...), but asking for structured
    `{"line": "...", "emotion": "..."}` output and returning
    (narration_text, emotion) instead of a bare string.

    The emotion vocabulary/instruction/parsing all live in emotion.py so
    agent.py and campaign/improviser.py ask for and parse the exact same
    shape. Raises exactly what llm_client.complete raises (LLMError,
    connection errors, ...) — callers already wrap this in the same
    try/except they used around the old bare `llm_client.complete(...)`
    call, so a network/model failure surfaces identically to before this
    existed; only a successful reply's SHAPE changed.
    """
    reply = llm_client.complete(
        f"{system_prompt}\n\n{STRUCTURED_REPLY_INSTRUCTION}",
        [{"role": "user", "content": prompt}],
    )
    return parse_structured_reply(reply)


def _send_manager_report(worker_id, producer, report_type, task, narration, extra=None):
    """Manager -> operator feedback surface. One message type
    (`manager_report`) with payload discriminator
    report_type: "milestone" | "blocker" | "escalation" — deliberately NOT
    `status_update`, which the feed hides by default (heartbeat flood filter).
    """
    payload = {"report_type": report_type, "task": task, "narration": narration}
    if extra:
        payload.update(extra)
    return producer.send(build_message(worker_id, "operator", "manager_report", payload))
