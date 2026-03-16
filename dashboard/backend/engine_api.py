from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(title="SMC Engine API", version="1.0.0")


_state_reader: Optional[Callable[[], Dict[str, Any]]] = None


def set_state_reader(reader: Callable[[], Dict[str, Any]]) -> None:
    global _state_reader
    _state_reader = reader


def _read_state() -> Dict[str, Any]:
    if _state_reader is None:
        return {
            "engine": "OFF",
            "market": "CLOSED",
            "nifty": None,
            "banknifty": None,
            "signals": [],
            "trades": [],
            "timestamp": None,
        }
    try:
        return _state_reader()
    except Exception:
        return {
            "engine": "OFF",
            "market": "CLOSED",
            "nifty": None,
            "banknifty": None,
            "signals": [],
            "trades": [],
            "timestamp": None,
        }

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://stockswithgaurav.com",
        "https://www.stockswithgaurav.com",
    ],
    allow_origin_regex=r"https://.*\.(vercel\.app|railway\.app)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _normalise_timestamp(ts: Optional[str]) -> str:
    if ts:
        return ts
    return datetime.utcnow().isoformat() + "Z"


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "smc-engine"}


@app.get("/api/status")
def api_status() -> Dict[str, Any]:
    state = _read_state()
    return {
        "engine": state.get("engine", "OFF"),
        "market": state.get("market", "CLOSED"),
        "timestamp": _normalise_timestamp(state.get("timestamp")),
    }


@app.get("/api/market")
def api_market() -> Dict[str, Any]:
    state = _read_state()
    return {
        "nifty": state.get("nifty"),
        "banknifty": state.get("banknifty"),
        "timestamp": _normalise_timestamp(state.get("timestamp")),
    }


@app.get("/api/signals")
def api_signals() -> Dict[str, Any]:
    state = _read_state()
    signals: List[Dict[str, Any]] = list(state.get("signals") or [])
    return {
        "signals": signals,
        "count": len(signals),
        "timestamp": _normalise_timestamp(state.get("timestamp")),
    }


@app.get("/api/trades")
def api_trades() -> Dict[str, Any]:
    state = _read_state()
    trades: List[Dict[str, Any]] = list(state.get("trades") or [])
    return {
        "trades": trades,
        "count": len(trades),
        "timestamp": _normalise_timestamp(state.get("timestamp")),
    }


def start_api_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Start the FastAPI engine API using uvicorn. Intended to run in a background thread."""
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    server.run()

