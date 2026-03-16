import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("start_web")

# Add repo root to Python path (in Railway container this is /app)
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, repo_root)

port = int(os.environ.get("PORT", 8000))
log.info("Railway PORT=%s, binding to 0.0.0.0:%d", os.environ.get("PORT", "(not set)"), port)

from dashboard.backend.main import app
import uvicorn

if __name__ == "__main__":
    log.info("Starting uvicorn on 0.0.0.0:%d …", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
