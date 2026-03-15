#!/usr/bin/env python3
"""Start FastAPI for Railway. Reads PORT from env (no shell expansion)."""
import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    from dashboard.backend.main import app
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)
