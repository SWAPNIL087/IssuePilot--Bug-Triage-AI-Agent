FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY triageguard ./triageguard
RUN pip install --no-cache-dir -e ".[api]"
COPY docs ./docs

RUN useradd --create-home app && chown -R app /app
USER app

EXPOSE 8000
# Hosts like Render set $PORT; default to 8000 locally.
CMD ["sh", "-c", "uvicorn triageguard.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
