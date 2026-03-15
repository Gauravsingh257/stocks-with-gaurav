# Web Service — FastAPI dashboard backend for Railway
# Use this for the "web" service. Engine uses Dockerfile.engine.

FROM python:3.11-slim

WORKDIR /app

# Install dashboard backend deps (uvicorn, FastAPI, etc.)
COPY requirements-railway.txt .
RUN pip install --no-cache-dir -r requirements-railway.txt

# Copy code
COPY . .

# Use Python starter so PORT is read from env (no shell expansion issues)
ENV PORT=8080
CMD ["python", "scripts/start_web.py"]
