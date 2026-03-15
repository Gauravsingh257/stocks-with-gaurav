# Web Service — FastAPI dashboard backend for Railway
# Use this for the "web" service. Engine uses Dockerfile.engine.

FROM python:3.11-slim

WORKDIR /app

# Install dashboard backend deps (uvicorn, FastAPI, etc.)
COPY requirements-railway.txt .
RUN pip install --no-cache-dir -r requirements-railway.txt

# Copy code
COPY . .

# Railway sets PORT; default 8080 for local runs
ENV PORT=8080
CMD ["sh", "-c", "exec uvicorn dashboard.backend.main:app --host 0.0.0.0 --port ${PORT}"]
