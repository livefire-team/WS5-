FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY livefire/ ./livefire/
COPY agents/ ./agents/
COPY fixtures/ ./fixtures/
RUN useradd --create-home --uid 10001 runner
USER 10001
# One drill per container start. DRILL_ID, MODE and SCENARIO come from the job execution.
ENTRYPOINT ["python", "-m", "livefire.runner"]
