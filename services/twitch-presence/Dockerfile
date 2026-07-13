FROM python:3.12-slim

# Unbuffered stdout/stderr — long-lived loop, same reasoning as log-shipper:
# without this, log lines sit block-buffered indefinitely and `docker logs`
# shows nothing while the process is healthy.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# No dependencies to install (stdlib only) — requirements.txt kept for
# convention/consistency with the other services.
COPY services/twitch-presence/presence.py /app/presence.py

ENTRYPOINT ["python3", "/app/presence.py"]
