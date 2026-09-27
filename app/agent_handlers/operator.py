"""
agent_handlers/operator.py
Any-role `operator_message` handler (split out of app/agent.py): direct
operator chat, answered with an `operator_reply`.
"""
from message_bus import build_message
from agent_state import write_state

from .common import _complete_with_emotion


def handle_operator_message(worker_id, agent_config, llm_client, producer, msg,
                            state_path=None, coding_backend=None):
    """Direct operator -> worker channel (message-api's default type). Any
    role handles it. Lightweight by design: LLM reply only, NO tmux demo
    side effects. Always answers `to: "operator"` with an operator_reply.
    """
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
        ))
        return

    print(f"[agent:{worker_id}] {narration}")
    if state_path:
        write_state(state_path, "speaking", action="replied to operator", bubble=narration, emotion=emotion)
    producer.send(build_message(
        worker_id, "operator", "operator_reply",
        {"narration": narration},
    ))
