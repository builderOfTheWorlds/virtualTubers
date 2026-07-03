FROM python:3.12-slim

WORKDIR /app

COPY services/log-shipper/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/log-shipper/shipper.py /app/shipper.py

ENTRYPOINT ["python3", "/app/shipper.py"]
