# Web Service — FastAPI dashboard backend for Railway
# Use this for the "web" service. Engine uses Dockerfile.engine.

FROM python:3.11-slim

WORKDIR /app

# Install dashboard backend deps (uvicorn, FastAPI, etc.)
COPY requirements-railway.txt .
RUN pip install --no-cache-dir -r requirements-railway.txt

# Copy code
COPY . .

# Entrypoint script reads PORT from env (Railway sets it at runtime)
COPY scripts/railway_web_start.sh /railway_web_start.sh
RUN sed -i 's/\r$//' /railway_web_start.sh && chmod +x /railway_web_start.sh

ENV PORT=8080
CMD ["/railway_web_start.sh"]
