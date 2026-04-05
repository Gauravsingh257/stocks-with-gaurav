"""
TradeGraph integration hooks for smc_mtf_engine_v4.py

Provides 3 functions to plug into the main engine:
    1. build_trade_graph(sig, regime)  → called after scoring, before ACTIVE_TRADES.append
    2. update_trade_graph_trail(trade) → called when trailing stop moves
    3. close_trade_graph(trade)        → called when trade closes (WIN/LOSS)

Storage: JSON files in trade_graphs/ directory, one per trade.
The graph is also attached to the trade dict as trade["_graph_id"] for reference.

Dashboard API: GET /api/trades/{id}/graph → returns the graph JSON.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

# Lazy import to avoid circular deps at module level
_TradeGraph = None

def _get_trade_graph_class():
    global _TradeGraph
    if _TradeGraph is None:
        from services.trade_graph import TradeGraph
        _TradeGraph = TradeGraph
    return _TradeGraph


GRAPH_DIR = Path("trade_graphs")
GRAPH_DIR.mkdir(exist_ok=True)


def build_trade_graph(sig: dict, regime: str = "NEUTRAL",
                      oi_data: dict | None = None) -> str:
    """Build and persist a TradeGraph from a scored signal.
    
    Call this in the SIGNAL_DISPATCH section, AFTER scoring/ranking,
    BEFORE appending to ACTIVE_TRADES.
    
    Returns: graph_id string (also stored as sig["_graph_id"])
    """
    try:
        TradeGraph = _get_trade_graph_class()
        
        symbol = sig.get("symbol", "UNKNOWN").replace(":", "_").replace(" ", "_")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        graph_id = f"TG_{symbol}_{ts}"
        
        graph = TradeGraph(trade_id=graph_id)
        graph.build_from_signal(sig, regime=regime, oi_data=oi_data)
        
        # Persist immediately
        path = GRAPH_DIR / f"{graph_id}.json"
        path.write_text(graph.to_json(), encoding="utf-8")
        
        # Attach ID to signal dict so it follows the trade through its lifecycle
        sig["_graph_id"] = graph_id
        
        logging.info(f"📊 TradeGraph built: {graph_id} ({len(graph.nodes)} nodes, {len(graph.edges)} edges)")
        return graph_id
        
    except Exception as e:
        logging.warning(f"TradeGraph build failed (non-blocking): {e}")
        return ""


def update_trade_graph_trail(trade: dict, stage: int, new_sl: float):
    """Add a trail move node to the trade's graph.
    
    Call this in monitor_active_trades() when trail_stage changes.
    """
    graph_id = trade.get("_graph_id")
    if not graph_id:
        return
    
    try:
        TradeGraph = _get_trade_graph_class()
        path = GRAPH_DIR / f"{graph_id}.json"
        if not path.exists():
            return
        
        graph = TradeGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))
        graph.add_trail_move(stage, new_sl)
        path.write_text(graph.to_json(), encoding="utf-8")
        
    except Exception as e:
        logging.debug(f"TradeGraph trail update failed: {e}")


def close_trade_graph(trade: dict) -> Optional[dict]:
    """Add outcome node and return the completed graph.
    
    Call this in the target-hit / SL-hit sections, AFTER setting
    trade["result"], trade["exit_price"], trade["exit_r"].
    
    Returns the full graph dict (for dashboard sync, content engine).
    """
    graph_id = trade.get("_graph_id")
    if not graph_id:
        return None
    
    try:
        TradeGraph = _get_trade_graph_class()
        path = GRAPH_DIR / f"{graph_id}.json"
        if not path.exists():
            return None
        
        graph = TradeGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))
        graph.add_outcome(trade)
        
        # Persist completed graph
        path.write_text(graph.to_json(), encoding="utf-8")
        
        result = trade.get("result", "?")
        logging.info(f"📊 TradeGraph closed: {graph_id} → {result}")
        
        return graph.to_dict()
        
    except Exception as e:
        logging.warning(f"TradeGraph close failed: {e}")
        return None


def get_trade_graph(graph_id: str) -> Optional[dict]:
    """Load a trade graph by ID. Used by dashboard API."""
    try:
        path = GRAPH_DIR / f"{graph_id}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def get_failure_analysis(graph_id: str) -> Optional[list]:
    """Get failure path analysis for a losing trade. Used by journal/debug."""
    try:
        TradeGraph = _get_trade_graph_class()
        path = GRAPH_DIR / f"{graph_id}.json"
        if not path.exists():
            return None
        
        graph = TradeGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))
        return graph.get_failure_path()
        
    except Exception:
        return None


def generate_content_prompt(graph_id: str, platform: str = "instagram") -> Optional[str]:
    """Generate a content prompt from a completed trade graph."""
    try:
        TradeGraph = _get_trade_graph_class()
        path = GRAPH_DIR / f"{graph_id}.json"
        if not path.exists():
            return None
        
        graph = TradeGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))
        return graph.to_content_prompt(platform=platform)
        
    except Exception:
        return None


def generate_video_prompt(graph_id: str) -> Optional[str]:
    """Generate a video script prompt from a completed trade graph."""
    try:
        TradeGraph = _get_trade_graph_class()
        path = GRAPH_DIR / f"{graph_id}.json"
        if not path.exists():
            return None
        
        graph = TradeGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))
        return graph.to_video_prompt()
        
    except Exception:
        return None


def get_website_graph(graph_id: str) -> Optional[dict]:
    """Get D3.js/React Flow compatible graph data for website."""
    try:
        TradeGraph = _get_trade_graph_class()
        path = GRAPH_DIR / f"{graph_id}.json"
        if not path.exists():
            return None
        
        graph = TradeGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))
        return graph.to_website_graph()
        
    except Exception:
        return None


def get_viral_content(graph_id: str) -> Optional[dict]:
    """Get amplified viral narrative from a completed trade graph."""
    try:
        TradeGraph = _get_trade_graph_class()
        from services.trade_graph import amplify_narrative

        path = GRAPH_DIR / f"{graph_id}.json"
        if not path.exists():
            return None

        graph = TradeGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))
        return amplify_narrative(graph)

    except Exception:
        return None


def get_video_scenes(graph_id: str) -> Optional[list]:
    """Get emotion-synced video scenes with SSML from a trade graph."""
    try:
        TradeGraph = _get_trade_graph_class()
        from services.trade_graph import graph_to_video_scenes

        path = GRAPH_DIR / f"{graph_id}.json"
        if not path.exists():
            return None

        graph = TradeGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))
        return graph_to_video_scenes(graph)

    except Exception:
        return None


def get_failure_patterns() -> Optional[dict]:
    """Run failure pattern analysis across all completed trade graphs."""
    try:
        from services.trade_graph import analyze_failure_patterns
        return analyze_failure_patterns(str(GRAPH_DIR))
    except Exception:
        return None


def get_telegram_narrative(graph_id: str) -> Optional[str]:
    """Get rich narrative-driven Telegram signal text."""
    try:
        TradeGraph = _get_trade_graph_class()
        from services.trade_graph import format_telegram_signal

        path = GRAPH_DIR / f"{graph_id}.json"
        if not path.exists():
            return None

        graph = TradeGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))
        return format_telegram_signal(graph)

    except Exception:
        return None
