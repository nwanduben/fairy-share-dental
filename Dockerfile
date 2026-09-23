FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend/ backend/
COPY config/ config/

# Render/Fly/Koyeb inject PORT
ENV PORT=8000
CMD ["sh", "-c", "uvicorn app.main:create_app --factory --app-dir backend --host 0.0.0.0 --port ${PORT}"]
