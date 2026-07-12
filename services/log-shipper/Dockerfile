FROM python:3.12-slim

# Unbuffered stdout/stderr — this runs a long-lived loop that never exits,
# so Python's default block-buffering (stdout isn't a TTY here) means log
# lines can sit unflushed indefinitely; `docker logs` would show nothing
# even while the process is healthy and working.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY services/log-shipper/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/log-shipper/shipper.py /app/shipper.py

ENTRYPOINT ["python3", "/app/shipper.py"]
