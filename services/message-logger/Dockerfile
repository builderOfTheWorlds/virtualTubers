FROM python:3.12-slim

# Unbuffered stdout/stderr — this runs a long-lived loop that never exits,
# so Python's default block-buffering (stdout isn't a TTY here) means log
# lines can sit unflushed indefinitely; `docker logs` would show nothing
# even while the process is healthy and working.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY services/message-logger/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app/message_bus.py /app/message_bus.py
COPY app/log_filter_control.py /app/log_filter_control.py
COPY services/message-logger/logger.py /app/logger.py

ENTRYPOINT ["python3", "/app/logger.py"]
