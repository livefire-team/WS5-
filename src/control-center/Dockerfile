# Build from the src/ folder so the shared events module is included:
#   docker build -f src/control-center/Dockerfile -t control-center src
FROM python:3.12-slim-bookworm
WORKDIR /app
COPY control-center/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/livefire_events.py .
COPY control-center/app/ .
RUN useradd --create-home --uid 10001 appuser
USER 10001
EXPOSE 8000
# Container Apps ingress is the only way in, so its forwarded headers are trusted.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
