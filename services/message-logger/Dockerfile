FROM python:3.12-slim

WORKDIR /app

COPY services/message-logger/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app/message_bus.py /app/message_bus.py
COPY services/message-logger/logger.py /app/logger.py

ENTRYPOINT ["python3", "/app/logger.py"]
