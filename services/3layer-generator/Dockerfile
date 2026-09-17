FROM python:3.12-slim

WORKDIR /app

COPY services/3layer-generator/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# app/campaign/ is copied as a PACKAGE, not flattened: improviser.py does
# `from campaign.pack import ...`, so the directory and its __init__.py have to
# survive. llm_client.py sits at the top level because concurrent_llm.py
# imports it bare (`from llm_client import ...`).
COPY app/llm_client.py /app/llm_client.py
COPY app/campaign/ /app/campaign/

# The utility's own modules go flat: its package directory name starts with a
# digit and contains a hyphen, so it is never importable as a package — the
# modules import each other bare (`from config import ...`), exactly as
# utilities/3LayersWeeklyGeneration/tests/conftest.py arranges for the suite.
COPY utilities/3LayersWeeklyGeneration/src/ /app/

COPY services/3layer-generator/generation_store.py /app/generation_store.py
COPY services/3layer-generator/runner.py /app/runner.py
COPY services/3layer-generator/generator_api.py /app/generator_api.py
# Phase 3's publish stage: runner._run_publish imports both by bare name,
# and campaign_episode_builder imports episode_builder — both must sit flat
# next to runner.py, matching the rest of this image's layout.
COPY services/3layer-generator/episode_builder.py /app/episode_builder.py
COPY services/3layer-generator/campaign_episode_builder.py /app/campaign_episode_builder.py

CMD ["uvicorn", "generator_api:app", "--host", "0.0.0.0", "--port", "8000"]
