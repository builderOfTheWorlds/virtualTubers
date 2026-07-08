FROM python:3.12-slim

WORKDIR /app

COPY services/message-api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app/message_bus.py /app/message_bus.py
COPY app/worker_control.py /app/worker_control.py
COPY services/message-api/api.py /app/api.py

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
