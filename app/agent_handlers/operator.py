"""
agent_handlers/operator.py
Any-role `operator_message` handler (split out of app/agent.py): direct
operator chat, answered with an `operator_reply`.

The ashiorid_office Party Member (agent.office_role: party_member) never
speaks (decision U6): no LLM call, no bubble, and the reply is a non-text
acknowledgement ({"silent": true}).
"""
import logging

from message_bus import build_message, reply_ids
from agent_state import write_state
from office.roles import OfficeRole

from .common import _complete_with_emotion
from .office import office_role_of

log = logging.getLogger(__name__)


def handle_operator_message(worker_id, agent_config, llm_client, producer, msg,
                            state_path=None, coding_backend=None):
    """Direct operator -> worker channel (message-api's default type). Any
    role handles it. Lightweight by design: LLM reply only, NO tmux demo
    side effects. Always answers `to: "operator"` with an operator_reply.

    The office Party Member is silent (U6): no LLM call, no bubble; the
    reply payload is {"silent": true, "office_role": "party_member"}.
    """
    if office_role_of(agent_config) is OfficeRole.PARTY_MEMBER:
        log.debug("operator_message silent worker=%s office_role=party_member", worker_id)
        print(f"[agent:{worker_id}] operator message received — Party Member stays silent")
        if state_path:
            write_state(state_path, "idle", action="listened to the operator")
        producer.send(build_message(
            worker_id, "operator", "operator_reply",
            {"silent": True, "office_role": OfficeRole.PARTY_MEMBER.value},
            **reply_ids(msg),
        ))
        return

    message = msg.get("payload", {}).get("message", "(no message provided)")
    prompt = (
        f"The operator (your boss) just messaged you directly: {message}\n\n"
        "Reply in 1-3 sentences, in character, as if speaking to the stream."
    )

    if state_path:
        write_state(state_path, "thinking", action="replying to operator")

    try:
        narration, emotion = _complete_with_emotion(
            llm_client, agent_config.get("system_prompt", ""), prompt)
    except Exception as exc:
        print(f"[agent:{worker_id}] LLM call failed: {exc}")
        if state_path:
            write_state(state_path, "frustrated", action="failed replying to operator", bubble=f"Ugh... {exc}")
        producer.send(build_message(
            worker_id, "operator", "operator_reply",
            {"error": str(exc)},
            **reply_ids(msg),
        ))
        return

    print(f"[agent:{worker_id}] {narration}")
    if state_path:
        write_state(state_path, "speaking", action="replied to operator", bubble=narration, emotion=emotion)
    producer.send(build_message(
        worker_id, "operator", "operator_reply",
        {"narration": narration},
        **reply_ids(msg),
    ))
