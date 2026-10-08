"""Character-agent ("seat") handler for the live agent table (build plan P3.5).

Each D&D character runs in its own worker container; this module is what that
container does when the turn arbiter (app/turns.py, in the GM's container)
addresses it. All seats share ONE model on vLLM; the seat's identity is its
context (its brief), nothing else.

Two-pass round:
  1. ``think_request``  -> the seat reasons privately about what it wants to do
     (one LLM call, reasoning ON with a budget; the InstrumentedLLMClient
     wrapper streams the reasoning to this seat's own Thinking pane by itself)
     and answers ``think_done``.
  2. ``turn_assignment`` -> the seat speaks one line, using ITS OWN private
     intent plus the committed transcript passed BY VALUE in the message.
  3. ``retake``         -> the seat speaks again for the same assignment, told
     why the last line was rejected.

THE FAILURES THAT MUST NEVER HAPPEN:
  * U6: the private intent leaves the seat. It is stored only in this seat's
    local memory file; ``think_done`` carries {scene_id, round, seat, ok} and
    nothing else; ``character_reply`` carries only the spoken line.
  * The committed-transcript rule: the seat never learns other seats' lines
    from anywhere except ``turn_assignment.committed_transcript`` (no bus
    reads, no shared files).
"""
import json
import logging
import os
import re

from table import protocol

log = logging.getLogger(__name__)

ROLE = "table_seat"
MEMORY_MAX_SCENES = 20

# One surrounding pair of straight or curly double quotes, if present.
_QUOTE_RE = re.compile(r'^["\u201c](.*)["\u201d]$', re.DOTALL)

# Process-level caches so repeated brief_for() calls in one worker do not
# re-read the lore files or re-open the character DB.
_LORE_CACHE = {}
_CONN_CACHE = {}

THINK_INSTRUCTION = (
    "\n\nDecide privately what you want and what you will say or do. "
    "At most 40 words. Nobody else sees this."
)
SPEAK_INSTRUCTION = (
    "\n\nSpeak exactly one spoken line, at most 45 words. Spoken words only: "
    "no name label, no *actions*, no (asides), no narration."
)


def _load_lore(pack_dir):
    """Read {stem: text} of <pack_dir>/lore/*.md, cached per process."""
    if pack_dir in _LORE_CACHE:
        return _LORE_CACHE[pack_dir]
    lore = {}
    lore_dir = os.path.join(pack_dir, "lore")
    if os.path.isdir(lore_dir):
        for fname in sorted(os.listdir(lore_dir)):
            if not fname.endswith(".md"):
                continue
            stem = fname[:-3]
            path = os.path.join(lore_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lore[stem] = f.read()
            except OSError as exc:
                log.error("seat brief: cannot read lore %s: %s", path, exc)
    _LORE_CACHE[pack_dir] = lore
    return lore


def _get_conn():
    """Open (or reuse) the character DB connection, cached per process.

    Review fix (orchestrator): the generated version passed the agent config
    dict to character.config.load() as a file path; config comes from
    config/character.yaml + CHARACTER_DB_* env. A closed connection is reopened.
    """
    conn = _CONN_CACHE.get("conn")
    if conn is not None and not getattr(conn, "closed", 1):
        return conn
    from character import config as char_config
    from character import db as char_db
    conn = char_db.connect(char_config.load())
    _CONN_CACHE["conn"] = conn
    return conn


def _drop_conn():
    conn = _CONN_CACHE.pop("conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - already gone
            pass


def brief_for(agent_config, worker_id, unlocked=()):
    """The seat's brief from the character DB (production default).

    Reads ``agent_config["table"]``: ``character_slug`` (the cast id of this
    seat) and ``pack_dir`` (lore = {stem: text} of <pack_dir>/lore/*.md,
    cached per process); connects with ``character.config.load()`` +
    ``character.db.connect(cfg)`` (connection cached per process; on any DB
    error log ERROR and re-raise); then
    ``table.seat_brief.build_table_brief(conn, slug, lore=lore,
    unlocked=unlocked)``.
    """
    table_cfg = agent_config.get("table") or {}
    slug = table_cfg.get("character_slug")
    pack_dir = table_cfg.get("pack_dir")
    lore = _load_lore(pack_dir) if pack_dir else {}
    from table import seat_brief
    try:
        conn = _get_conn()
        text = seat_brief.build_table_brief(conn, slug, lore=lore, unlocked=unlocked)
        conn.rollback()       # read-only: never leave the session idle in a transaction
        return text
    except Exception:
        log.error("seat brief: character DB read failed for %s", worker_id)
        _drop_conn()
        raise


class SeatMemory:
    """Local JSON memory for one seat: <memory_dir>/table_seat_<worker_id>.json.

    Shape: {"scenes": {scene_id: {"order": n, "intents": {str(round): text},
    "assignment": {str(round): payload}}}}. Keeps at most MEMORY_MAX_SCENES
    scenes (drops the lowest "order" first).
    """

    def __init__(self, path):
        self.path = path
        self._data = {"scenes": {}}
        self._load()

    @classmethod
    def for_worker(cls, table_cfg, worker_id):
        memory_dir = (table_cfg or {}).get("memory_dir") \
            or os.environ.get("TABLE_SEAT_MEMORY_DIR") or "/tmp/table_seat"
        os.makedirs(memory_dir, exist_ok=True)
        path = os.path.join(memory_dir, f"table_seat_{worker_id}.json")
        return cls(path)

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("scenes"), dict):
                self._data = data
        except (OSError, ValueError):
            self._data = {"scenes": {}}

    def _save(self):
        from relay_io import atomic_write_json
        atomic_write_json(self.path, self._data)

    def _scene(self, scene_id):
        scenes = self._data["scenes"]
        if scene_id not in scenes:
            # max+1, not len(): after pruning len() can repeat a live order and the
            # prune would then drop the NEWEST scene (review fix).
            nxt = max((s.get("order", 0) for s in scenes.values()), default=-1) + 1
            scenes[scene_id] = {"order": nxt, "intents": {}, "assignment": {}}
        return scenes[scene_id]

    def _prune(self):
        scenes = self._data["scenes"]
        while len(scenes) > MEMORY_MAX_SCENES:
            lowest = min(scenes, key=lambda s: scenes[s].get("order", 0))
            del scenes[lowest]

    def intent(self, scene_id, round_):
        scene = self._data["scenes"].get(scene_id)
        if not scene:
            return None
        return scene.get("intents", {}).get(str(round_))

    def set_intent(self, scene_id, round_, text):
        scene = self._scene(scene_id)
        scene["intents"][str(round_)] = text
        self._prune()
        self._save()

    def assignment(self, scene_id, round_):
        scene = self._data["scenes"].get(scene_id)
        if not scene:
            return None
        return scene.get("assignment", {}).get(str(round_))

    def set_assignment(self, scene_id, round_, payload):
        scene = self._scene(scene_id)
        scene["assignment"][str(round_)] = payload
        self._prune()
        self._save()


def _render_transcript(transcript, seat_names):
    """One line per entry: "<name>: <text>" where name is the display name."""
    lines = []
    for entry in transcript or []:
        speaker = entry.get("speaker", "")
        name = (seat_names or {}).get(speaker, speaker)
        lines.append(f"{name}: {entry.get('text', '')}")
    return "\n".join(lines)


def _llm_call(llm_client, system, user, max_tokens, reasoning_budget):
    """One LLM call. Returns the spoken/reasoned content string.

    Prefers ``complete_stream`` (reasoning ON, budgeted); falls back to
    ``complete`` when the client has no ``complete_stream``.
    """
    messages = [{"role": "user", "content": user}]
    if hasattr(llm_client, "complete_stream"):
        result = llm_client.complete_stream(
            system, messages, max_tokens=max_tokens,
            reasoning_budget=reasoning_budget)
        if isinstance(result, tuple):
            return result[1]
        return result
    return llm_client.complete(system, messages)


def _strip_quotes(text):
    """Strip ONE surrounding pair of quotes ("" or “”) from `text`."""
    text = (text or "").strip()
    match = _QUOTE_RE.match(text)
    if match:
        return match.group(1).strip()
    return text


def _reply(producer, worker_id, msg, type_, payload):
    """Build and send a table reply to the arbiter (msg["from"])."""
    reply = protocol.build(
        type_, worker_id, msg["from"], payload,
        turn=msg.get("turn"),
        correlation_id=payload["scene_id"],
        causation_id=msg.get("id"))
    producer.send(reply)


def _guard(worker_id, agent_config, msg):
    """Common pre-checks. Returns (table_cfg, payload) or None to bail out."""
    if (agent_config or {}).get("role") != ROLE:
        return None
    try:
        payload = protocol.parse(msg)
    except protocol.ProtocolError as exc:
        log.warning("table seat: protocol error on %s: %s", msg.get("type"), exc)
        return None
    return (agent_config.get("table") or {}, payload)


def handle_think_request(worker_id, agent_config, llm_client, producer, msg,
                         state_path=None, coding_backend=None):
    """think_request -> one THINK call -> think_done (NO intent on the bus)."""
    checked = _guard(worker_id, agent_config, msg)
    if checked is None:
        return
    table_cfg, payload = checked
    if worker_id not in payload.get("seats", []):
        return

    scene_id = payload["scene_id"]
    round_ = payload["round"]
    memory = SeatMemory.for_worker(table_cfg, worker_id)
    think_cfg = table_cfg.get("think") or {}
    max_tokens = think_cfg.get("max_tokens", 1024)
    reasoning_budget = think_cfg.get("reasoning_budget", 256)

    try:
        brief = brief_for(agent_config, worker_id)
        user = _render_transcript(payload.get("committed_transcript"),
                                  table_cfg.get("seat_names"))
        content = _llm_call(llm_client, brief + THINK_INSTRUCTION, user,
                            max_tokens, reasoning_budget)
        memory.set_intent(scene_id, round_, content)
        log.debug("seat think ok worker=%s scene=%s round=%s intent_len=%d",
                  worker_id, scene_id, round_, len(content or ""))
        ok = True
    except Exception as exc:
        log.error("seat think failed worker=%s scene=%s round=%s: %s",
                  worker_id, scene_id, round_, exc)
        ok = False

    _reply(producer, worker_id, msg, "think_done",
           {"scene_id": scene_id, "round": round_, "seat": worker_id, "ok": ok})


def _speak(worker_id, agent_config, llm_client, producer, msg, table_cfg,
           scene_id, round_, seat, transcript, instruction, extra_user=""):
    """Shared SPEAK path for turn_assignment and retake.

    Builds the system (brief + SPEAK instruction) and user (rendered
    transcript + instruction + own intent + optional extra block), makes one
    LLM call, and replies character_reply.
    """
    memory = SeatMemory.for_worker(table_cfg, worker_id)
    speak_cfg = table_cfg.get("speak") or {}
    max_tokens = speak_cfg.get("max_tokens", 160)
    reasoning_budget = speak_cfg.get("reasoning_budget", 48)

    try:
        brief = brief_for(agent_config, worker_id)
        parts = [_render_transcript(transcript, table_cfg.get("seat_names"))]
        if instruction:
            parts.append(instruction)
        intent = memory.intent(scene_id, round_)
        if intent:
            parts.append("Your private intent (only you know this):\n" + intent)
        if extra_user:
            parts.append(extra_user)
        user = "\n\n".join(parts)
        content = _llm_call(llm_client, brief + SPEAK_INSTRUCTION, user,
                            max_tokens, reasoning_budget)
        text = _strip_quotes(content)
        log.debug("seat speak ok worker=%s scene=%s round=%s line_len=%d",
                  worker_id, scene_id, round_, len(text))
        _reply(producer, worker_id, msg, "character_reply",
               {"scene_id": scene_id, "round": round_, "seat": seat,
                "text": text, "took": True, "reason": ""})
    except Exception as exc:
        log.error("seat speak failed worker=%s scene=%s round=%s: %s",
                  worker_id, scene_id, round_, exc)
        _reply(producer, worker_id, msg, "character_reply",
               {"scene_id": scene_id, "round": round_, "seat": seat,
                "text": "", "took": False,
                "reason": f"llm_error: {type(exc).__name__}"})


def handle_turn_assignment(worker_id, agent_config, llm_client, producer, msg,
                           state_path=None, coding_backend=None):
    """turn_assignment -> store assignment -> one SPEAK call -> character_reply."""
    checked = _guard(worker_id, agent_config, msg)
    if checked is None:
        return
    table_cfg, payload = checked
    if payload.get("seat") != worker_id:
        return

    scene_id = payload["scene_id"]
    round_ = payload["round"]
    memory = SeatMemory.for_worker(table_cfg, worker_id)
    memory.set_assignment(scene_id, round_, payload)

    _speak(worker_id, agent_config, llm_client, producer, msg, table_cfg,
           scene_id, round_, worker_id,
           payload.get("committed_transcript"), payload.get("instruction"))


def handle_retake(worker_id, agent_config, llm_client, producer, msg,
                  state_path=None, coding_backend=None):
    """retake -> SPEAK again for the stored assignment, with the retake reason."""
    checked = _guard(worker_id, agent_config, msg)
    if checked is None:
        return
    table_cfg, payload = checked
    if payload.get("seat") != worker_id:
        return

    scene_id = payload["scene_id"]
    round_ = payload["round"]
    memory = SeatMemory.for_worker(table_cfg, worker_id)
    stored = memory.assignment(scene_id, round_)
    if stored is None:
        log.warning("seat retake: no stored assignment worker=%s scene=%s round=%s",
                    worker_id, scene_id, round_)
        return

    extra_user = (f"Your last line was rejected: {payload.get('reason', '')}. "
                  "Say it again, differently, following the rules.")
    _speak(worker_id, agent_config, llm_client, producer, msg, table_cfg,
           scene_id, round_, worker_id,
           stored.get("committed_transcript"), stored.get("instruction"),
           extra_user=extra_user)
