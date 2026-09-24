FROM python:3.12-slim

WORKDIR /app

COPY services/message-api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app/message_bus.py /app/message_bus.py
COPY app/worker_control.py /app/worker_control.py
COPY app/console_theme.py /app/console_theme.py
COPY config/themes/gogh_themes.json /config/themes/gogh_themes.json
COPY app/log_filter_control.py /app/log_filter_control.py
COPY app/log_prune.py /app/log_prune.py
# Episode upload (POST /replays): the store, the validator, and everything
# the validator's dry-run render imports — replay/revoice and the two
# stdlib-only modules replay itself pulls in at import time.
COPY app/episode_store.py /app/episode_store.py
COPY app/episode_validator.py /app/episode_validator.py
# Symbolic voice registry (roundtable_stream_design.md v1.1 §7.2). The
# validator's V1 stage (§8.1) checks that a show header's voice NAMES are known
# — a pure config lookup, which is why it can run here where /data/voices is
# deliberately NOT mounted. revoice.py imports it too. Without this file the
# validator raises ImportError on every upload.
COPY app/voice_registry.py /app/voice_registry.py
COPY app/session_log_parser.py /app/session_log_parser.py
COPY app/replay.py /app/replay.py
COPY app/revoice.py /app/revoice.py
COPY app/agent_state.py /app/agent_state.py
COPY app/audio_player.py /app/audio_player.py
COPY services/message-api/api.py /app/api.py

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
