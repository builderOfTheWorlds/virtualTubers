FROM python:3.12-slim

WORKDIR /app

COPY services/control-panel/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/control-panel/panel.py /app/panel.py
COPY services/control-panel/templates /app/templates
COPY services/control-panel/static /app/static

CMD ["uvicorn", "panel:app", "--host", "0.0.0.0", "--port", "8000"]
