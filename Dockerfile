# Web Service — FastAPI dashboard backend for Railway
# Use this for the "web" service. Engine uses Dockerfile.engine.

FROM python:3.11-slim

WORKDIR /app

# Install dashboard backend deps (uvicorn, FastAPI, etc.)
COPY requirements-railway.txt .
RUN pip install --no-cache-dir -r requirements-railway.txt

# Copy code
COPY . .

# Repo root on PYTHONPATH so "dashboard" is findable when running scripts/start_web.py
ENV PYTHONPATH=/app
ENV PORT=8000
CMD ["python", "scripts/start_web.py"]
