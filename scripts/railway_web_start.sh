#!/bin/sh
# Start FastAPI for Railway web service. Reads PORT from env (Railway sets it).
port="${PORT:-8080}"
exec uvicorn dashboard.backend.main:app --host 0.0.0.0 --port "$port"
