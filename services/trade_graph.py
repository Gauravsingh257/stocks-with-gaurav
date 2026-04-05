"""
TradeGraph — Graph-based trade reasoning system.

Captures the full causal chain of every trade:
  Market Regime → Structure Detection → Confluence → Entry → Management → Outcome

Inspired by code-review-graph's SQLite+NetworkX pattern, adapted for trading logic.

Usage:
    from services.trade_graph import TradeGraph, GraphNode, GraphEdge

    graph = TradeGraph()
    graph.build_from_signal(signal_dict)      # On signal generation
    graph.add_outcome(trade_dict)             # On trade close
    graph.to_narrative()                      # For content engine
    graph.to_video_scenes()                   # For video engine
    graph.to_dict()                           # For dashboard API / JSON storage
"""

from __future__ import annotations

import json
import time
import hashlib
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional
from pathlib import Path


# ---------------------------------------------------------------------------
# Node Types
# ---------------------------------------------------------------------------

class NodeKind(str, Enum):
    # Market context
    REGIME = "REGIME"               # Bullish/Bearish/Neutral market state
    SESSION = "SESSION"             # Trading session context (time, opening range)
    
    # Structure detection (SMC)
    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"  # Stop hunt / liquidity grab
    CHOCH = "CHOCH"                 # Change of Character
    BOS = "BOS"                     # Break of Structure
    ORDER_BLOCK = "ORDER_BLOCK"     # OB zone
    FVG = "FVG"                     # Fair Value Gap
    DISPLACEMENT = "DISPLACEMENT"   # Smart money displacement candle
    
    # Confluence / Scoring
    CONFLUENCE = "CONFLUENCE"       # Combined score node
    HTF_BIAS = "HTF_BIAS"          # Higher timeframe alignment
    OI_SIGNAL = "OI_SIGNAL"        # Open interest confirmation
    VOLUME = "VOLUME"               # Volume expansion signal
    
    # Trade execution
    ENTRY = "ENTRY"                 # Entry point
    STOP_LOSS = "STOP_LOSS"         # Initial SL
    TARGET = "TARGET"               # TP level
    TRAIL_MOVE = "TRAIL_MOVE"       # Trailing stop adjustment
    
    # Outcome
    OUTCOME = "OUTCOME"             # Final result (WIN/LOSS/TRAIL_WIN)


class EdgeKind(str, Enum):
    CREATES = "CREATES"             # Regime → conditions
    DETECTS = "DETECTS"             # Candle action → structure
    CONFIRMS = "CONFIRMS"           # Structure confirms another
    INVALIDATES = "INVALIDATES"     # Structure breaks/invalidates
    TRIGGERS = "TRIGGERS"           # Confluence → entry decision
    MANAGES = "MANAGES"             # Entry → trail/exit logic
    RESOLVES = "RESOLVES"           # Trade → outcome
    ALIGNS = "ALIGNS"              # HTF bias aligns with LTF
    SWEEPS = "SWEEPS"              # Liquidity sweep before reversal


# ---------------------------------------------------------------------------
# Graph Nodes & Edges
# ---------------------------------------------------------------------------

@dataclass
class GraphNode:
    id: str                         # Unique: e.g., "OB_NIFTY50_1712345678"
    kind: str                       # NodeKind value
    label: str                      # Human-readable: "Bullish OB at 22,380"
    timestamp: str                  # ISO format
    data: dict = field(default_factory=dict)  # Kind-specific payload
    
    # Content engine fields
    emotion: str = ""               # tension, insight, confidence, triumph
    narrative_text: str = ""        # Pre-written story fragment
    visual_hint: str = ""           # Chart annotation hint


@dataclass
class GraphEdge:
    source: str                     # Source node ID
    target: str                     # Target node ID
    kind: str                       # EdgeKind value
    label: str = ""                 # e.g., "confirms entry"
    weight: float = 1.0             # Strength of relationship


# ---------------------------------------------------------------------------
# TradeGraph
# ---------------------------------------------------------------------------

class TradeGraph:
    """Directed acyclic graph representing the causal chain of a single trade."""
    
    def __init__(self, trade_id: str = ""):
        self.trade_id = trade_id or f"TG_{int(time.time())}"
        self.nodes: list[GraphNode] = []
        self.edges: list[GraphEdge] = []
        self._node_map: dict[str, GraphNode] = {}
    
    # --- Build from engine data ---
    
    def _make_id(self, kind: str, symbol: str) -> str:
        ts = int(time.time() * 1000)
        raw = f"{kind}_{symbol}_{ts}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]
    
    def _add_node(self, kind: str, label: str, timestamp: str = "",
                  data: dict | None = None, emotion: str = "",
                  narrative: str = "", visual: str = "") -> str:
        nid = self._make_id(kind, label)
        node = GraphNode(
            id=nid,
            kind=kind,
            label=label,
            timestamp=timestamp or datetime.now().isoformat(),
            data=data or {},
            emotion=emotion,
            narrative_text=narrative,
            visual_hint=visual,
        )
        self.nodes.append(node)
        self._node_map[nid] = node
        return nid
    
    def _add_edge(self, source: str, target: str, kind: str,
                  label: str = "", weight: float = 1.0):
        self.edges.append(GraphEdge(
            source=source, target=target, kind=kind,
            label=label, weight=weight,
        ))
    
    def build_from_signal(self, sig: dict, regime: str = "NEUTRAL",
                          oi_data: dict | None = None) -> "TradeGraph":
        """Build the full graph from an engine signal dict.
        
        This is called in scan_symbol() AFTER scoring, BEFORE adding to ACTIVE_TRADES.
        
        Args:
            sig: Signal dict from detect_setup_*() with smc_score/smc_breakdown
            regime: Current market regime string
            oi_data: Optional OI intelligence snapshot
        """
        symbol = sig.get("symbol", "UNKNOWN")
        direction = sig.get("direction", "?")
        setup = sig.get("setup", "?")
        ts = datetime.now().isoformat()
        breakdown = sig.get("smc_breakdown", {})
        
        # --- 1. REGIME NODE ---
        regime_id = self._add_node(
            NodeKind.REGIME, f"Market: {regime}",
            timestamp=ts,
            data={"regime": regime, "symbol": symbol},
            emotion="tension" if regime == "BEARISH" else "neutral",
            narrative=f"The market was in a {regime.lower()} phase.",
            visual="regime_banner",
        )
        
        # --- 2. HTF BIAS ---
        htf_bias = sig.get("htf_bias") or direction
        htf_id = self._add_node(
            NodeKind.HTF_BIAS, f"HTF Bias: {htf_bias}",
            timestamp=ts,
            data={"bias": htf_bias, "timeframe": "1H"},
            emotion="context",
            narrative=f"Higher timeframe showed {htf_bias.lower()} structure.",
            visual="htf_trend_arrow",
        )
        self._add_edge(regime_id, htf_id, EdgeKind.CREATES, "sets bias")
        
        # --- 3. LIQUIDITY SWEEP (if detected) ---
        prev_id = htf_id
        if sig.get("sweep_detected"):
            sweep_id = self._add_node(
                NodeKind.LIQUIDITY_SWEEP, "Liquidity Swept",
                timestamp=ts,
                data={"direction": direction},
                emotion="tension",
                narrative="Smart money swept stops below the key level — a classic trap.",
                visual="sweep_highlight",
            )
            self._add_edge(prev_id, sweep_id, EdgeKind.SWEEPS, "liquidity grabbed")
            prev_id = sweep_id
        
        # --- 4. DISPLACEMENT (if detected) ---
        disp = sig.get("displacement_event")
        if disp:
            disp_id = self._add_node(
                NodeKind.DISPLACEMENT, f"Displacement: {disp.get('confidence', 'medium')}",
                timestamp=ts,
                data=disp,
                emotion="shift",
                narrative=f"A {disp.get('confidence', 'strong')} displacement candle confirmed smart money intent.",
                visual="displacement_candle",
            )
            self._add_edge(prev_id, disp_id, EdgeKind.DETECTS, "displacement detected")
            prev_id = disp_id
        
        # --- 5. CHOCH ---
        if sig.get("choch_time") or breakdown.get("choch", 0) > 0:
            choch_label = f"CHoCH at {sig.get('choch_level', '?')}"
            choch_id = self._add_node(
                NodeKind.CHOCH, choch_label,
                timestamp=str(sig.get("choch_time", ts)),
                data={
                    "level": sig.get("choch_level"),
                    "score": breakdown.get("choch", 0),
                },
                emotion="insight",
                narrative=f"Change of Character confirmed — trend reversal signal at {sig.get('choch_level', '?')}.",
                visual="choch_line",
            )
            self._add_edge(prev_id, choch_id, EdgeKind.DETECTS, "structure shift")
            prev_id = choch_id
        
        # --- 6. BOS ---
        if sig.get("bos_confirmed") or breakdown.get("bos", 0) > 0:
            bos_id = self._add_node(
                NodeKind.BOS, f"BOS Confirmed ({direction})",
                timestamp=ts,
                data={"confirmed": True, "score": breakdown.get("bos", 0)},
                emotion="confidence",
                narrative=f"Break of Structure confirmed the new {direction.lower()} trend.",
                visual="bos_break_line",
            )
            self._add_edge(prev_id, bos_id, EdgeKind.CONFIRMS, "structure broken")
            prev_id = bos_id
        
        # --- 7. ORDER BLOCK ---
        ob = sig.get("ob")
        if ob:
            ob_label = f"OB: {ob[0]}-{ob[1]}"
            ob_id = self._add_node(
                NodeKind.ORDER_BLOCK, ob_label,
                timestamp=ts,
                data={"low": ob[0], "high": ob[1], "score": breakdown.get("ob", 0)},
                emotion="precision",
                narrative=f"Demand zone identified between {ob[0]} and {ob[1]}.",
                visual="ob_zone_box",
            )
            self._add_edge(prev_id, ob_id, EdgeKind.DETECTS, "OB formed")
            prev_id = ob_id
        
        # --- 8. FVG ---
        fvg = sig.get("fvg")
        if fvg:
            fvg_label = f"FVG: {fvg[0]}-{fvg[1]}"
            fvg_id = self._add_node(
                NodeKind.FVG, fvg_label,
                timestamp=ts,
                data={"low": fvg[0], "high": fvg[1], "score": breakdown.get("fvg", 0)},
                emotion="insight",
                narrative=f"Fair Value Gap (imbalance) between {fvg[0]} and {fvg[1]} — price wants to fill this.",
                visual="fvg_gap_highlight",
            )
            # FVG confirms OB if both exist
            if ob:
                self._add_edge(ob_id, fvg_id, EdgeKind.CONFIRMS, "OB+FVG confluence")
            else:
                self._add_edge(prev_id, fvg_id, EdgeKind.DETECTS, "FVG formed")
            prev_id = fvg_id
        
        # --- 9. CONFLUENCE SCORE ---
        score = sig.get("smc_score", 0)
        confluence_id = self._add_node(
            NodeKind.CONFLUENCE, f"Score: {score}/10",
            timestamp=ts,
            data={
                "total": score,
                "breakdown": breakdown,
                "risk_mult": sig.get("risk_mult", 1.0),
                "confidence": sig.get("confidence", "B"),
            },
            emotion="decision",
            narrative=f"Confluence score: {score}/10. {_score_narrative(score)}",
            visual="score_badge",
        )
        self._add_edge(prev_id, confluence_id, EdgeKind.CONFIRMS, f"scored {score}")
        
        # --- 10. OI SIGNAL (if available) ---
        if oi_data and oi_data.get("signal"):
            oi_id = self._add_node(
                NodeKind.OI_SIGNAL, f"OI: {oi_data['signal']}",
                timestamp=ts,
                data=oi_data,
                emotion="confirmation",
                narrative=f"Open Interest data {_oi_narrative(oi_data)}.",
                visual="oi_badge",
            )
            self._add_edge(oi_id, confluence_id, EdgeKind.ALIGNS, "OI confirms")
        
        # --- 11. ENTRY ---
        entry_id = self._add_node(
            NodeKind.ENTRY, f"{direction} @ {sig.get('entry', '?')}",
            timestamp=ts,
            data={
                "price": sig.get("entry"),
                "type": sig.get("entry_type", "LIMIT"),
                "setup": setup,
            },
            emotion="confidence",
            narrative=f"Entry triggered: {direction} at {sig.get('entry', '?')}.",
            visual="entry_arrow",
        )
        self._add_edge(confluence_id, entry_id, EdgeKind.TRIGGERS, "entry fired")
        
        # --- 12. SL ---
        sl_id = self._add_node(
            NodeKind.STOP_LOSS, f"SL: {sig.get('sl', '?')}",
            timestamp=ts,
            data={"price": sig.get("sl"), "original": True},
            emotion="risk",
            narrative=f"Stop loss set at {sig.get('sl', '?')} — risk defined.",
            visual="sl_line",
        )
        self._add_edge(entry_id, sl_id, EdgeKind.MANAGES, "risk defined")
        
        # --- 13. TARGET ---
        target_id = self._add_node(
            NodeKind.TARGET, f"Target: {sig.get('target', '?')}",
            timestamp=ts,
            data={
                "price": sig.get("target"),
                "rr": sig.get("rr", 2.0),
            },
            emotion="ambition",
            narrative=f"Target at {sig.get('target', '?')} for {sig.get('rr', 2.0)}R reward.",
            visual="target_line",
        )
        self._add_edge(entry_id, target_id, EdgeKind.MANAGES, "target set")
        
        return self
    
    def add_trail_move(self, stage: int, new_sl: float, timestamp: str = ""):
        """Called during trade management when trailing stop moves."""
        trail_id = self._add_node(
            NodeKind.TRAIL_MOVE, f"Trail Stage {stage}: SL→{new_sl}",
            timestamp=timestamp or datetime.now().isoformat(),
            data={"stage": stage, "new_sl": new_sl},
            emotion="control",
            narrative=f"Trailing stop moved to {new_sl} — locking in profits.",
            visual="trail_arrow",
        )
        # Connect to entry node
        entry_nodes = [n for n in self.nodes if n.kind == NodeKind.ENTRY]
        if entry_nodes:
            self._add_edge(entry_nodes[0].id, trail_id, EdgeKind.MANAGES, f"trail stage {stage}")
    
    def add_outcome(self, trade: dict):
        """Called when trade closes. Adds the final OUTCOME node."""
        result = trade.get("result", "UNKNOWN")
        exit_r = trade.get("exit_r", 0)
        exit_price = trade.get("exit_price", 0)
        
        emoji_map = {"WIN": "triumph", "LOSS": "lesson", "TRAIL_WIN": "satisfaction"}
        
        outcome_id = self._add_node(
            NodeKind.OUTCOME, f"{result}: {exit_r:+.1f}R",
            timestamp=datetime.now().isoformat(),
            data={
                "result": result,
                "exit_r": exit_r,
                "exit_price": exit_price,
                "trail_stage": trade.get("trail_stage", 0),
            },
            emotion=emoji_map.get(result, "neutral"),
            narrative=_outcome_narrative(result, exit_r, exit_price),
            visual="result_badge",
        )
        # Connect from entry
        entry_nodes = [n for n in self.nodes if n.kind == NodeKind.ENTRY]
        if entry_nodes:
            self._add_edge(entry_nodes[0].id, outcome_id, EdgeKind.RESOLVES, f"{result}")
    
    # --- Serialization ---
    
    def to_dict(self) -> dict:
        """Full graph as JSON-serializable dict."""
        return {
            "trade_id": self.trade_id,
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [asdict(e) for e in self.edges],
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)
    
    @classmethod
    def from_dict(cls, d: dict) -> "TradeGraph":
        graph = cls(trade_id=d.get("trade_id", ""))
        for nd in d.get("nodes", []):
            node = GraphNode(**nd)
            graph.nodes.append(node)
            graph._node_map[node.id] = node
        for ed in d.get("edges", []):
            graph.edges.append(GraphEdge(**ed))
        return graph
    
    # --- Content Engine Outputs ---
    
    def to_narrative(self) -> list[dict]:
        """Convert graph to ordered narrative chain for content engine.
        
        Returns list of story beats in causal order, each with:
            step, label, emotion, narrative_text, visual_hint
        """
        # Topological order via the edge chain
        ordered = self._topo_sort()
        return [
            {
                "step": i + 1,
                "kind": n.kind,
                "label": n.label,
                "emotion": n.emotion,
                "narrative": n.narrative_text,
                "visual": n.visual_hint,
                "data": n.data,
            }
            for i, n in enumerate(ordered)
        ]
    
    def to_content_prompt(self, platform: str = "instagram") -> str:
        """Generate a ready-to-use prompt for the content LLM."""
        narrative = self.to_narrative()
        
        # Extract key trade facts
        entry_node = next((n for n in self.nodes if n.kind == NodeKind.ENTRY), None)
        outcome_node = next((n for n in self.nodes if n.kind == NodeKind.OUTCOME), None)
        
        symbol = entry_node.data.get("setup", "?") if entry_node else "?"
        result_text = outcome_node.label if outcome_node else "Open trade"
        
        chain_text = "\n".join(
            f"  Step {s['step']}: [{s['kind']}] {s['label']}"
            for s in narrative
        )
        
        prompts = {
            "instagram": INSTAGRAM_PROMPT_TEMPLATE,
            "twitter": TWITTER_PROMPT_TEMPLATE,
            "linkedin": LINKEDIN_PROMPT_TEMPLATE,
        }
        
        template = prompts.get(platform, INSTAGRAM_PROMPT_TEMPLATE)
        return template.format(
            chain=chain_text,
            result=result_text,
            narrative_json=json.dumps(narrative, indent=2, default=str),
        )
    
    def to_video_scenes(self) -> list[dict]:
        """Convert graph to video scene sequence with timing + emotion."""
        ordered = self._topo_sort()
        scenes = []
        
        for i, node in enumerate(ordered):
            scene = VIDEO_SCENE_MAP.get(node.kind, DEFAULT_SCENE)
            scenes.append({
                "scene_number": i + 1,
                "kind": node.kind,
                "label": node.label,
                "narrative": node.narrative_text,
                "duration_sec": scene["duration"],
                "emotion": node.emotion or scene["emotion"],
                "visual_type": node.visual_hint or scene["visual"],
                "transition": scene.get("transition", "cut"),
                "voice_tone": scene.get("voice_tone", "neutral"),
            })
        
        return scenes
    
    def to_video_prompt(self) -> str:
        """Generate prompt for AI video script generator."""
        scenes = self.to_video_scenes()
        return VIDEO_PROMPT_TEMPLATE.format(
            scenes_json=json.dumps(scenes, indent=2, default=str),
            total_duration=sum(s["duration_sec"] for s in scenes),
        )
    
    def to_website_graph(self) -> dict:
        """Export for D3.js / React Flow visualization on website."""
        return {
            "nodes": [
                {
                    "id": n.id,
                    "type": n.kind,
                    "label": n.label,
                    "emotion": n.emotion,
                    "data": n.data,
                    "position": {"x": 0, "y": i * 120},  # Auto-layout hint
                }
                for i, n in enumerate(self.nodes)
            ],
            "edges": [
                {
                    "id": f"e_{e.source}_{e.target}",
                    "source": e.source,
                    "target": e.target,
                    "label": e.label,
                    "type": e.kind,
                    "animated": e.kind in (EdgeKind.TRIGGERS, EdgeKind.RESOLVES),
                }
                for e in self.edges
            ],
        }
    
    # --- Analysis ---
    
    def get_failure_path(self) -> list[dict] | None:
        """For losing trades: trace which edge in the chain failed.
        
        Returns the sequence of nodes leading to the loss, with annotations
        about which confirmations were weak or missing.
        """
        outcome = next((n for n in self.nodes if n.kind == NodeKind.OUTCOME), None)
        if not outcome or outcome.data.get("result") != "LOSS":
            return None
        
        # Walk backward from outcome to find weak links
        narrative = self.to_narrative()
        failure_analysis = []
        
        for step in narrative:
            weakness = None
            if step["kind"] == NodeKind.CONFLUENCE:
                score = step["data"].get("total", 0)
                if score < 6:
                    weakness = f"Low confluence ({score}/10) — should have been ≥6"
                breakdown = step["data"].get("breakdown", {})
                missing = [k for k, v in breakdown.items() if v == 0]
                if missing:
                    weakness = f"Missing: {', '.join(missing)}"
            
            elif step["kind"] == NodeKind.HTF_BIAS:
                if step["data"].get("bias") == "NEUTRAL":
                    weakness = "No clear HTF bias — risky entry"
            
            elif step["kind"] in (NodeKind.CHOCH, NodeKind.BOS):
                if step["data"].get("score", 0) < 2:
                    weakness = f"Weak {step['kind']} confirmation (score: {step['data'].get('score', 0)})"
            
            failure_analysis.append({
                **step,
                "weakness": weakness,
                "contributed_to_loss": weakness is not None,
            })
        
        return failure_analysis
    
    def get_strength_summary(self) -> dict:
        """Summarize what made this trade strong/weak."""
        present = {n.kind for n in self.nodes}
        
        strengths = []
        weaknesses = []
        
        if NodeKind.LIQUIDITY_SWEEP in present:
            strengths.append("Liquidity swept before entry")
        else:
            weaknesses.append("No liquidity sweep detected")
        
        if NodeKind.DISPLACEMENT in present:
            strengths.append("Displacement confirmed smart money")
        
        if NodeKind.CHOCH in present and NodeKind.BOS in present:
            strengths.append("Both CHoCH + BOS confirmed")
        elif NodeKind.CHOCH not in present:
            weaknesses.append("No CHoCH detected")
        
        if NodeKind.ORDER_BLOCK in present and NodeKind.FVG in present:
            strengths.append("OB+FVG confluence")
        elif NodeKind.FVG not in present:
            weaknesses.append("No FVG confirmation")
        
        confluence = next((n for n in self.nodes if n.kind == NodeKind.CONFLUENCE), None)
        if confluence:
            score = confluence.data.get("total", 0)
            if score >= 7:
                strengths.append(f"High confluence ({score}/10)")
            elif score < 5:
                weaknesses.append(f"Low confluence ({score}/10)")
        
        return {
            "strengths": strengths,
            "weaknesses": weaknesses,
            "strength_ratio": len(strengths) / max(1, len(strengths) + len(weaknesses)),
        }
    
    # --- Internal ---
    
    def _topo_sort(self) -> list[GraphNode]:
        """Simple topological sort following edge chain order."""
        if not self.nodes:
            return []
        
        # Build adjacency
        in_degree: dict[str, int] = {n.id: 0 for n in self.nodes}
        adj: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        
        for e in self.edges:
            if e.target in in_degree:
                in_degree[e.target] += 1
            if e.source in adj:
                adj[e.source].append(e.target)
        
        # Kahn's algorithm
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        result = []
        
        while queue:
            nid = queue.pop(0)
            if nid in self._node_map:
                result.append(self._node_map[nid])
            for neighbor in adj.get(nid, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        # Add any nodes not reached (disconnected)
        seen = {n.id for n in result}
        for n in self.nodes:
            if n.id not in seen:
                result.append(n)
        
        return result


# ---------------------------------------------------------------------------
# Narrative Helpers
# ---------------------------------------------------------------------------

def _score_narrative(score: int) -> str:
    if score >= 8:
        return "A+ setup — all confluences aligned."
    elif score >= 6:
        return "Strong setup with solid confirmation."
    elif score >= 4:
        return "Moderate setup — some confirmation missing."
    return "Weak setup — proceed with caution."


def _oi_narrative(oi_data: dict) -> str:
    sig = oi_data.get("signal", "neutral")
    if "bullish" in sig.lower():
        return "confirmed bullish positioning — call writers dominant"
    elif "bearish" in sig.lower():
        return "showed bearish unwinding — put writers in control"
    return "was neutral — no clear directional bias from options"


def _outcome_narrative(result: str, exit_r: float, exit_price: float) -> str:
    if result == "WIN":
        return f"Target hit at {exit_price}. Profit: {exit_r:+.1f}R. The setup played out perfectly."
    elif result == "LOSS":
        return f"Stop loss hit at {exit_price}. Loss: {exit_r:+.1f}R. Time to analyze what went wrong."
    return f"Trailed exit at {exit_price}. Result: {exit_r:+.1f}R. Partial profits captured."


# ---------------------------------------------------------------------------
# Video Scene Mapping
# ---------------------------------------------------------------------------

VIDEO_SCENE_MAP = {
    NodeKind.REGIME: {
        "duration": 3, "emotion": "tension", "visual": "market_overview",
        "transition": "fade_in", "voice_tone": "serious",
    },
    NodeKind.HTF_BIAS: {
        "duration": 3, "emotion": "context", "visual": "htf_chart",
        "transition": "zoom_out", "voice_tone": "analytical",
    },
    NodeKind.LIQUIDITY_SWEEP: {
        "duration": 4, "emotion": "tension", "visual": "sweep_animation",
        "transition": "reveal", "voice_tone": "dramatic",
    },
    NodeKind.DISPLACEMENT: {
        "duration": 3, "emotion": "shift", "visual": "displacement_candle",
        "transition": "zoom_in", "voice_tone": "excited",
    },
    NodeKind.CHOCH: {
        "duration": 4, "emotion": "insight", "visual": "choch_markup",
        "transition": "highlight", "voice_tone": "knowing",
    },
    NodeKind.BOS: {
        "duration": 3, "emotion": "confidence", "visual": "bos_break",
        "transition": "cut", "voice_tone": "confident",
    },
    NodeKind.ORDER_BLOCK: {
        "duration": 4, "emotion": "precision", "visual": "ob_zone_box",
        "transition": "draw", "voice_tone": "precise",
    },
    NodeKind.FVG: {
        "duration": 3, "emotion": "insight", "visual": "fvg_highlight",
        "transition": "reveal", "voice_tone": "analytical",
    },
    NodeKind.CONFLUENCE: {
        "duration": 4, "emotion": "decision", "visual": "score_overlay",
        "transition": "build_up", "voice_tone": "decisive",
    },
    NodeKind.ENTRY: {
        "duration": 3, "emotion": "confidence", "visual": "entry_arrow",
        "transition": "zoom_in", "voice_tone": "confident",
    },
    NodeKind.STOP_LOSS: {
        "duration": 2, "emotion": "risk", "visual": "sl_line",
        "transition": "cut", "voice_tone": "measured",
    },
    NodeKind.TARGET: {
        "duration": 2, "emotion": "ambition", "visual": "target_line",
        "transition": "cut", "voice_tone": "optimistic",
    },
    NodeKind.OUTCOME: {
        "duration": 5, "emotion": "triumph", "visual": "result_chart",
        "transition": "reveal", "voice_tone": "triumphant",
    },
}

DEFAULT_SCENE = {
    "duration": 3, "emotion": "neutral", "visual": "generic",
    "transition": "cut", "voice_tone": "neutral",
}


# ---------------------------------------------------------------------------
# Content Prompt Templates
# ---------------------------------------------------------------------------

INSTAGRAM_PROMPT_TEMPLATE = """You are a viral trading content creator for @StocksWithGaurav (Instagram).

TRADE NARRATIVE GRAPH (causal chain — each step caused the next):
{chain}

RESULT: {result}

RULES:
1. Write a 5-slide Instagram carousel
2. Start with the OUTCOME or a contrarian hook (Slide 1)
3. Walk backward through the graph — each slide = one node
4. Use the narrative text from each node as inspiration, NOT verbatim
5. Max 8-12 words per line, 3-4 lines per slide
6. Last slide = CTA (follow for daily SMC setups)
7. Use storytelling, not textbook explanations
8. Tone: confident, educational, slightly provocative

OUTPUT FORMAT:
Slide 1 (HOOK): [text]
Slide 2 (SETUP): [text]  
Slide 3 (STRUCTURE): [text]
Slide 4 (EXECUTION): [text]
Slide 5 (CTA): [text]
Caption: [full caption with hashtags]

FULL NARRATIVE DATA:
{narrative_json}"""


TWITTER_PROMPT_TEMPLATE = """You are writing a Twitter/X thread for @StocksWithGaurav about a trade.

TRADE NARRATIVE GRAPH:
{chain}

RESULT: {result}

RULES:
1. Thread of 5-7 tweets
2. Tweet 1: Hook — the result or contrarian take (make people stop scrolling)
3. Each subsequent tweet maps to one step in the graph
4. Use precise numbers (entry, SL, target, R-multiple)
5. Last tweet: Follow + what to watch tomorrow
6. No emojis spam — max 1 per tweet
7. Tone: sharp, data-driven, slightly edgy

OUTPUT: number each tweet 1/ 2/ 3/ etc.

FULL NARRATIVE DATA:
{narrative_json}"""


LINKEDIN_PROMPT_TEMPLATE = """You are writing a LinkedIn post for Gaurav (StocksWithGaurav) about trade analysis.

TRADE NARRATIVE GRAPH:
{chain}

RESULT: {result}

RULES:
1. Single post, 150-250 words
2. Open with a lesson or insight (NOT "I took a trade today")
3. Walk through the graph as a decision-making framework
4. Emphasize the PROCESS, not the profit
5. End with a question to drive engagement
6. Professional but accessible tone
7. Add 3-5 relevant hashtags

FULL NARRATIVE DATA:
{narrative_json}"""


# ---------------------------------------------------------------------------
# Video Prompt Template
# ---------------------------------------------------------------------------

VIDEO_PROMPT_TEMPLATE = """Generate a trading reel script from this scene graph.

SCENES (in order):
{scenes_json}

TOTAL DURATION: ~{total_duration} seconds

RULES:
1. Each scene = one shot in the reel
2. Write voiceover text for each scene (max 2 sentences)
3. Match the voice_tone specified for each scene
4. First 3 seconds MUST hook the viewer (use the outcome or a question)
5. Include chart annotation descriptions (what to highlight visually)
6. End with CTA: "Follow @StocksWithGaurav for daily setups"
7. Pacing: fast for tension, slow for insight, fast for result

OUTPUT FORMAT:
For each scene:
- SCENE [N] ([duration]s) — [emotion]
- VISUAL: [what appears on screen]
- VOICEOVER: [what the narrator says]
- TEXT OVERLAY: [on-screen text, if any]
- TRANSITION: [how to move to next scene]"""


# ---------------------------------------------------------------------------
# UPGRADE 1 — Narrative Amplifier (Viral Content Engine)
# ---------------------------------------------------------------------------

# Maps NodeKind → storytelling role in the viral narrative
_VIRAL_ROLE = {
    NodeKind.REGIME:           "context",
    NodeKind.HTF_BIAS:         "context",
    NodeKind.LIQUIDITY_SWEEP:  "twist",
    NodeKind.DISPLACEMENT:     "smart_money",
    NodeKind.CHOCH:            "smart_money",
    NodeKind.BOS:              "smart_money",
    NodeKind.ORDER_BLOCK:      "setup",
    NodeKind.FVG:              "setup",
    NodeKind.CONFLUENCE:       "setup",
    NodeKind.OI_SIGNAL:        "confirmation",
    NodeKind.VOLUME:           "confirmation",
    NodeKind.ENTRY:            "execution",
    NodeKind.STOP_LOSS:        "execution",
    NodeKind.TARGET:           "execution",
    NodeKind.TRAIL_MOVE:       "management",
    NodeKind.OUTCOME:          "result",
}

# Emotion hooks per outcome type — scroll-stopping openers
_HOOK_TEMPLATES = {
    "WIN": [
        "Everyone shorted here. That's why it went up.",
        "{symbol} gave +{exit_r}R — but NOT how you think.",
        "This trade looked wrong to 90% of traders.",
        "Retail panicked. Smart money loaded. Here's what happened.",
    ],
    "LOSS": [
        "I lost {exit_r}R on this trade. Here's exactly why.",
        "This setup had everything — and still failed.",
        "Even A+ setups lose. This is what that looks like.",
        "I'm sharing this loss so you don't repeat it.",
    ],
    "TRAIL_WIN": [
        "Got stopped out in profit — and I'm happy about it.",
        "This trade was heading to target, then reversed. Still banked {exit_r}R.",
        "Trail stop saved this trade from becoming a loss.",
    ],
}


def amplify_narrative(graph: "TradeGraph") -> dict:
    """Convert a logical trade graph into viral storytelling components.

    Returns a dict with: hook, conflict, twist, smart_money, execution,
    result, lesson, slides (ready-to-post Instagram carousel).
    """
    ordered = graph._topo_sort()

    # Extract key nodes
    outcome = next((n for n in ordered if n.kind == NodeKind.OUTCOME), None)
    entry = next((n for n in ordered if n.kind == NodeKind.ENTRY), None)
    sweep = next((n for n in ordered if n.kind == NodeKind.LIQUIDITY_SWEEP), None)
    ob = next((n for n in ordered if n.kind == NodeKind.ORDER_BLOCK), None)
    fvg = next((n for n in ordered if n.kind == NodeKind.FVG), None)
    confluence = next((n for n in ordered if n.kind == NodeKind.CONFLUENCE), None)

    result = outcome.data.get("result", "WIN") if outcome else "WIN"
    exit_r = abs(outcome.data.get("exit_r", 0)) if outcome else 0
    regime_node = next((n for n in ordered if n.kind == NodeKind.REGIME), None)
    symbol = regime_node.data.get("symbol", "?") if regime_node else "?"
    direction = entry.label.split("@")[0].strip() if entry and "@" in entry.label else "?"

    # 1. HOOK — scroll-stopping opener
    templates = _HOOK_TEMPLATES.get(result, _HOOK_TEMPLATES["WIN"])
    hook_idx = hash(graph.trade_id) % len(templates)
    hook = templates[hook_idx].format(
        symbol=symbol, exit_r=f"{exit_r:.1f}",
        direction=direction,
    )

    # 2. CONFLICT — what retail got wrong
    if sweep:
        conflict = "Retail traders got trapped — liquidity was swept below the key level."
    elif result == "LOSS":
        conflict = "The setup looked clean, but the market had other plans."
    else:
        conflict = "Price broke structure. Most traders chased it. Wrong move."

    # 3. TWIST — the liquidity event
    if sweep:
        twist = sweep.narrative_text
    else:
        choch = next((n for n in ordered if n.kind == NodeKind.CHOCH), None)
        twist = choch.narrative_text if choch else "The structure shifted — smart money stepped in."

    # 4. SMART MONEY — OB + FVG zone
    ob_text = ob.label if ob else "Demand zone identified"
    fvg_text = fvg.label if fvg else ""
    smart_money = f"{ob_text} + {fvg_text}" if fvg_text else ob_text

    # 5. EXECUTION
    entry_price = entry.data.get("price", "?") if entry else "?"
    sl_node = next((n for n in ordered if n.kind == NodeKind.STOP_LOSS), None)
    sl_price = sl_node.data.get("price", "?") if sl_node else "?"
    tgt_node = next((n for n in ordered if n.kind == NodeKind.TARGET), None)
    tgt_price = tgt_node.data.get("price", "?") if tgt_node else "?"
    execution = f"Entry: {entry_price} | SL: {sl_price} | Target: {tgt_price}"

    # 6. RESULT
    result_text = outcome.narrative_text if outcome else "Trade open"

    # 7. LESSON
    if result == "WIN":
        lesson = "Follow liquidity, not price. Smart money leaves footprints."
    elif result == "LOSS":
        # Find the weak link
        weak = ""
        if confluence:
            score = confluence.data.get("total", 0)
            missing = [k for k, v in confluence.data.get("breakdown", {}).items() if v == 0]
            if missing:
                weak = f"Missing: {', '.join(missing)}."
            elif score < 6:
                weak = f"Confluence was only {score}/10."
        lesson = f"Not every A+ setup wins. {weak} Risk management saved the account."
    else:
        lesson = "Trail your stops. Protect profits. Let the market decide."

    # 8. SLIDES — ready-to-post Instagram carousel
    score_text = f"{confluence.data.get('total', '?')}/10" if confluence else "?"
    slides = [
        {"slide": 1, "type": "HOOK", "text": hook},
        {"slide": 2, "type": "CONFLICT", "text": conflict},
        {"slide": 3, "type": "TWIST", "text": twist},
        {"slide": 4, "type": "SETUP", "text": f"{smart_money}\nConfluence: {score_text}"},
        {"slide": 5, "type": "EXECUTION", "text": execution},
        {"slide": 6, "type": "RESULT", "text": result_text},
        {"slide": 7, "type": "LESSON", "text": lesson},
    ]

    return {
        "hook": hook,
        "conflict": conflict,
        "twist": twist,
        "smart_money": smart_money,
        "execution": execution,
        "result": result_text,
        "lesson": lesson,
        "slides": slides,
    }


# ---------------------------------------------------------------------------
# UPGRADE 2 — Video SceneGraph with SSML + Emotion Sync
# ---------------------------------------------------------------------------

# Emotion → voice delivery mapping
_VOICE_DELIVERY = {
    "tension":      {"pace": "slow", "volume": "low", "pause_ms": 600},
    "curiosity":    {"pace": "medium", "volume": "medium", "pause_ms": 400},
    "fear":         {"pace": "fast", "volume": "medium", "pause_ms": 200},
    "surprise":     {"pace": "medium", "volume": "high", "pause_ms": 500},
    "insight":      {"pace": "slow", "volume": "medium", "pause_ms": 500},
    "confidence":   {"pace": "medium", "volume": "high", "pause_ms": 300},
    "authority":    {"pace": "slow", "volume": "high", "pause_ms": 400},
    "precision":    {"pace": "slow", "volume": "medium", "pause_ms": 300},
    "decision":     {"pace": "medium", "volume": "high", "pause_ms": 400},
    "risk":         {"pace": "fast", "volume": "low", "pause_ms": 200},
    "control":      {"pace": "medium", "volume": "medium", "pause_ms": 300},
    "triumph":      {"pace": "slow", "volume": "high", "pause_ms": 600},
    "satisfaction":  {"pace": "slow", "volume": "high", "pause_ms": 500},
    "lesson":       {"pace": "slow", "volume": "medium", "pause_ms": 500},
    "neutral":      {"pace": "medium", "volume": "medium", "pause_ms": 300},
    "shift":        {"pace": "medium", "volume": "medium", "pause_ms": 400},
    "context":      {"pace": "medium", "volume": "low", "pause_ms": 300},
    "ambition":     {"pace": "medium", "volume": "medium", "pause_ms": 300},
    "confirmation": {"pace": "medium", "volume": "medium", "pause_ms": 300},
}

# Scene-level voiceover templates (viral, not robotic)
_SCENE_VOICE_TEMPLATES = {
    NodeKind.REGIME: [
        "The market was {regime}...",
        "{regime} regime. Everyone was positioned one way.",
    ],
    NodeKind.HTF_BIAS: [
        "Higher timeframes showed {bias} structure.",
        "The bigger picture was clear: {bias}.",
    ],
    NodeKind.LIQUIDITY_SWEEP: [
        "Then it happened. Stops got hunted.",
        "Price dipped below the level. Retail panicked.",
        "Liquidity was swept. This was the trap.",
    ],
    NodeKind.DISPLACEMENT: [
        "A massive candle appeared. Smart money was in.",
        "Displacement confirmed. This wasn't random.",
    ],
    NodeKind.CHOCH: [
        "Change of Character. The trend was shifting.",
        "Structure broke in the other direction. CHoCH confirmed.",
    ],
    NodeKind.BOS: [
        "Break of Structure. Now we had confirmation.",
        "BOS locked in. The new trend was real.",
    ],
    NodeKind.ORDER_BLOCK: [
        "Right here. The institutional zone. Order Block formed.",
        "This is where smart money placed their orders.",
    ],
    NodeKind.FVG: [
        "Fair Value Gap. An imbalance price wants to fill.",
        "The gap confirmed it. Institutions were aggressive.",
    ],
    NodeKind.CONFLUENCE: [
        "Everything aligned. Score: {score} out of 10.",
        "Confluence stacked. This was the setup.",
    ],
    NodeKind.ENTRY: [
        "Entry triggered. {direction} at {price}.",
        "I took the trade. {direction} at {price}.",
    ],
    NodeKind.STOP_LOSS: [
        "Stop loss set. Risk defined.",
        "Risk managed. SL at {price}.",
    ],
    NodeKind.TARGET: [
        "Target set for {rr}R reward.",
        "Aiming for {price}. The math was clear.",
    ],
    NodeKind.TRAIL_MOVE: [
        "Trail moved. Locking in profits.",
        "Stop ratcheted up. Stage {stage}.",
    ],
    NodeKind.OUTCOME: [
        "{result}. {narrative}",
    ],
}


def graph_to_video_scenes(graph: "TradeGraph") -> list[dict]:
    """Convert TradeGraph → emotion-synced video scene graph with SSML.

    Each scene has: voice text, SSML markup, emotion, duration, visual cues,
    music cue, and transition. Ready for ElevenLabs / PlayHT voiceover.
    """
    ordered = graph._topo_sort()
    viral = amplify_narrative(graph)
    scenes = []

    # Scene 0: HOOK (always first — the outcome or contrarian take)
    hook_delivery = _VOICE_DELIVERY.get("curiosity", _VOICE_DELIVERY["neutral"])
    scenes.append({
        "scene_number": 0,
        "scene_type": "HOOK",
        "duration_sec": 3,
        "voice_text": viral["hook"],
        "ssml": _to_ssml(viral["hook"], pause_after_ms=hook_delivery["pause_ms"]),
        "emotion": "curiosity",
        "voice_delivery": hook_delivery,
        "visual": "text_on_black_bg",
        "text_overlay": viral["hook"],
        "music_cue": "tension_build",
        "transition": "fade_in",
    })

    for i, node in enumerate(ordered):
        scene_cfg = VIDEO_SCENE_MAP.get(node.kind, DEFAULT_SCENE)
        emotion = node.emotion or scene_cfg["emotion"]
        delivery = _VOICE_DELIVERY.get(emotion, _VOICE_DELIVERY["neutral"])

        # Pick voice template and fill from data
        templates = _SCENE_VOICE_TEMPLATES.get(node.kind, ["{narrative}"])
        tmpl_idx = hash(node.id) % len(templates)
        voice_text = templates[tmpl_idx].format(
            regime=node.data.get("regime", "unknown"),
            bias=node.data.get("bias", "unknown"),
            score=node.data.get("total", "?"),
            direction=node.data.get("type", "?"),
            price=node.data.get("price", "?"),
            rr=node.data.get("rr", "?"),
            stage=node.data.get("stage", "?"),
            result=node.data.get("result", "?"),
            narrative=node.narrative_text,
        )

        # Music cue based on emotion
        music_map = {
            "tension": "dark_ambient", "fear": "dark_ambient",
            "insight": "discovery_melody", "confidence": "upbeat_subtle",
            "triumph": "victory_swell", "satisfaction": "victory_swell",
            "lesson": "reflective_piano", "risk": "tension_pulse",
            "decision": "build_up", "precision": "focused_beats",
        }

        scenes.append({
            "scene_number": i + 1,
            "scene_type": node.kind,
            "duration_sec": scene_cfg["duration"],
            "voice_text": voice_text,
            "ssml": _to_ssml(voice_text, pause_after_ms=delivery["pause_ms"]),
            "emotion": emotion,
            "voice_delivery": delivery,
            "visual": node.visual_hint or scene_cfg["visual"],
            "text_overlay": node.label,
            "music_cue": music_map.get(emotion, "ambient"),
            "transition": scene_cfg.get("transition", "cut"),
        })

    # Final CTA scene
    scenes.append({
        "scene_number": len(scenes),
        "scene_type": "CTA",
        "duration_sec": 3,
        "voice_text": "Follow StocksWithGaurav for daily SMC setups.",
        "ssml": _to_ssml("Follow StocksWithGaurav for daily SMC setups.", pause_after_ms=0),
        "emotion": "confidence",
        "voice_delivery": _VOICE_DELIVERY["confidence"],
        "visual": "logo_card",
        "text_overlay": "@StocksWithGaurav",
        "music_cue": "outro",
        "transition": "fade_out",
    })

    return scenes


def _to_ssml(text: str, pause_after_ms: int = 300) -> str:
    """Wrap text in SSML with natural pauses for ElevenLabs/PlayHT.

    Adds sentence-level breaks and a trailing pause for emotion sync.
    """
    # Split on sentence boundaries
    import re
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())

    ssml_parts = ["<speak>"]
    for j, sent in enumerate(sentences):
        ssml_parts.append(f"  {sent}")
        if j < len(sentences) - 1:
            ssml_parts.append('  <break time="300ms"/>')
    if pause_after_ms > 0:
        ssml_parts.append(f'  <break time="{pause_after_ms}ms"/>')
    ssml_parts.append("</speak>")

    return "\n".join(ssml_parts)


# ---------------------------------------------------------------------------
# UPGRADE 4 — Failure Pattern Engine (Strategy Optimizer)
# ---------------------------------------------------------------------------

def analyze_failure_patterns(graph_dir: str | Path = "trade_graphs") -> dict:
    """Scan all completed trade graphs and extract recurring failure patterns.

    Returns aggregate failure analysis:
    - top failure reasons (ranked)
    - conditions that correlate with losses
    - recommendations for strategy improvement
    """
    graph_path = Path(graph_dir)
    if not graph_path.exists():
        return {"error": "No trade graphs found", "patterns": []}

    losses = []
    wins = []
    all_graphs = []

    for f in sorted(graph_path.glob("TG_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            outcome_node = next(
                (n for n in data.get("nodes", []) if n.get("kind") == NodeKind.OUTCOME),
                None,
            )
            if not outcome_node:
                continue  # Trade still open, skip

            result = outcome_node.get("data", {}).get("result", "UNKNOWN")
            entry = {
                "file": f.name,
                "trade_id": data.get("trade_id", ""),
                "result": result,
                "exit_r": outcome_node.get("data", {}).get("exit_r", 0),
                "nodes": data.get("nodes", []),
            }
            all_graphs.append(entry)
            if result == "LOSS":
                losses.append(entry)
            elif result in ("WIN", "TRAIL_WIN"):
                wins.append(entry)
        except Exception:
            continue

    if not all_graphs:
        return {"total_trades": 0, "patterns": [], "recommendations": []}

    # --- Extract failure conditions ---
    failure_conditions = []
    for loss in losses:
        conditions = _extract_conditions(loss["nodes"])
        conditions["trade_id"] = loss["trade_id"]
        conditions["exit_r"] = loss["exit_r"]
        failure_conditions.append(conditions)

    # --- Extract win conditions for comparison ---
    win_conditions = []
    for win in wins:
        conditions = _extract_conditions(win["nodes"])
        win_conditions.append(conditions)

    # --- Find patterns: what do losses have in common? ---
    patterns = _find_failure_patterns(failure_conditions, win_conditions)

    # --- Build recommendations ---
    recommendations = _build_recommendations(patterns)

    return {
        "total_trades": len(all_graphs),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / max(1, len(all_graphs)) * 100, 1),
        "avg_loss_r": round(
            sum(l["exit_r"] for l in losses) / max(1, len(losses)), 2
        ),
        "patterns": patterns,
        "failure_conditions": failure_conditions,
        "recommendations": recommendations,
    }


def _extract_conditions(nodes: list[dict]) -> dict:
    """Extract tradeable conditions from a graph's node list."""
    node_kinds = {n.get("kind") for n in nodes}

    confluence = next(
        (n for n in nodes if n.get("kind") == NodeKind.CONFLUENCE), None
    )
    score = confluence.get("data", {}).get("total", 0) if confluence else 0
    breakdown = confluence.get("data", {}).get("breakdown", {}) if confluence else {}
    missing_components = [k for k, v in breakdown.items() if v == 0]

    regime = next(
        (n for n in nodes if n.get("kind") == NodeKind.REGIME), None
    )
    regime_val = regime.get("data", {}).get("regime", "UNKNOWN") if regime else "UNKNOWN"

    htf = next(
        (n for n in nodes if n.get("kind") == NodeKind.HTF_BIAS), None
    )
    htf_bias = htf.get("data", {}).get("bias", "UNKNOWN") if htf else "UNKNOWN"

    return {
        "has_sweep": NodeKind.LIQUIDITY_SWEEP in node_kinds,
        "has_displacement": NodeKind.DISPLACEMENT in node_kinds,
        "has_choch": NodeKind.CHOCH in node_kinds,
        "has_bos": NodeKind.BOS in node_kinds,
        "has_ob": NodeKind.ORDER_BLOCK in node_kinds,
        "has_fvg": NodeKind.FVG in node_kinds,
        "has_oi": NodeKind.OI_SIGNAL in node_kinds,
        "has_volume": NodeKind.VOLUME in node_kinds,
        "confluence_score": score,
        "missing_components": missing_components,
        "regime": regime_val,
        "htf_bias": htf_bias,
    }


def _find_failure_patterns(losses: list[dict], wins: list[dict]) -> list[dict]:
    """Compare loss conditions vs win conditions to find patterns."""
    patterns = []

    if not losses:
        return patterns

    n_loss = len(losses)
    n_win = max(1, len(wins))

    # 1. Missing component analysis
    all_missing = Counter()
    for lc in losses:
        for m in lc.get("missing_components", []):
            all_missing[m] += 1

    for comp, count in all_missing.most_common():
        pct = round(count / n_loss * 100, 1)
        if pct >= 30:  # Appears in at least 30% of losses
            # Compare with wins
            win_missing = sum(
                1 for wc in wins if comp in wc.get("missing_components", [])
            )
            win_pct = round(win_missing / n_win * 100, 1)
            if pct > win_pct + 10:  # Significantly more common in losses
                patterns.append({
                    "type": "missing_component",
                    "component": comp,
                    "loss_pct": pct,
                    "win_pct": win_pct,
                    "severity": "high" if pct >= 60 else "medium",
                    "description": f"'{comp}' missing in {pct}% of losses vs {win_pct}% of wins",
                })

    # 2. Low confluence score
    avg_loss_score = sum(l.get("confluence_score", 0) for l in losses) / n_loss
    avg_win_score = sum(w.get("confluence_score", 0) for w in wins) / n_win if wins else 0

    if avg_loss_score < avg_win_score - 1:
        patterns.append({
            "type": "low_confluence",
            "avg_loss_score": round(avg_loss_score, 1),
            "avg_win_score": round(avg_win_score, 1),
            "severity": "high",
            "description": f"Losing trades avg score {avg_loss_score:.1f} vs winning {avg_win_score:.1f}",
        })

    # 3. Missing sweep analysis
    loss_sweep_rate = sum(1 for l in losses if l.get("has_sweep")) / n_loss * 100
    win_sweep_rate = sum(1 for w in wins if w.get("has_sweep")) / n_win * 100 if wins else 0

    if win_sweep_rate > loss_sweep_rate + 15:
        patterns.append({
            "type": "no_liquidity_sweep",
            "loss_sweep_pct": round(loss_sweep_rate, 1),
            "win_sweep_pct": round(win_sweep_rate, 1),
            "severity": "high" if win_sweep_rate - loss_sweep_rate > 30 else "medium",
            "description": f"Sweep present in {win_sweep_rate:.0f}% of wins but only {loss_sweep_rate:.0f}% of losses",
        })

    # 4. HTF mismatch
    loss_neutral_htf = sum(1 for l in losses if l.get("htf_bias") == "NEUTRAL") / n_loss * 100
    win_neutral_htf = sum(1 for w in wins if w.get("htf_bias") == "NEUTRAL") / n_win * 100 if wins else 0

    if loss_neutral_htf > win_neutral_htf + 15:
        patterns.append({
            "type": "htf_mismatch",
            "loss_neutral_pct": round(loss_neutral_htf, 1),
            "win_neutral_pct": round(win_neutral_htf, 1),
            "severity": "medium",
            "description": f"Neutral HTF in {loss_neutral_htf:.0f}% of losses vs {win_neutral_htf:.0f}% of wins",
        })

    # 5. No FVG confirmation
    loss_no_fvg = sum(1 for l in losses if not l.get("has_fvg")) / n_loss * 100
    win_no_fvg = sum(1 for w in wins if not w.get("has_fvg")) / n_win * 100 if wins else 0

    if loss_no_fvg > win_no_fvg + 15:
        patterns.append({
            "type": "no_fvg",
            "loss_no_fvg_pct": round(loss_no_fvg, 1),
            "win_no_fvg_pct": round(win_no_fvg, 1),
            "severity": "medium",
            "description": f"FVG missing in {loss_no_fvg:.0f}% of losses vs {win_no_fvg:.0f}% of wins",
        })

    # Sort by severity
    sev_order = {"high": 0, "medium": 1, "low": 2}
    patterns.sort(key=lambda p: sev_order.get(p["severity"], 3))

    return patterns


def _build_recommendations(patterns: list[dict]) -> list[str]:
    """Convert failure patterns into actionable strategy recommendations."""
    recs = []

    for p in patterns:
        if p["type"] == "missing_component":
            comp = p["component"]
            recs.append(
                f"❌ Avoid entries when '{comp}' is missing — "
                f"it appears in {p['loss_pct']}% of losing trades"
            )
        elif p["type"] == "low_confluence":
            recs.append(
                f"📊 Raise minimum confluence threshold — "
                f"losses avg {p['avg_loss_score']}/10 vs wins {p['avg_win_score']}/10"
            )
        elif p["type"] == "no_liquidity_sweep":
            recs.append(
                f"💧 Prefer setups WITH liquidity sweep — "
                f"win rate jumps from {p['loss_sweep_pct']:.0f}% to {p['win_sweep_pct']:.0f}%"
            )
        elif p["type"] == "htf_mismatch":
            recs.append(
                f"📐 Skip trades when HTF bias is NEUTRAL — "
                f"neutral HTF is {p['loss_neutral_pct']:.0f}% of losses"
            )
        elif p["type"] == "no_fvg":
            recs.append(
                f"📭 Require FVG confirmation — "
                f"missing in {p['loss_no_fvg_pct']:.0f}% of losses"
            )

    if not recs:
        recs.append("✅ No clear failure patterns detected. Strategy is performing well.")

    return recs


# ---------------------------------------------------------------------------
# Telegram Rich Narrative (for signal alerts)
# ---------------------------------------------------------------------------

def format_telegram_signal(graph: "TradeGraph") -> str:
    """Generate rich, narrative-driven Telegram alert from a trade graph.

    Output format:
        🔥 A+ SETUP
        NIFTY — LONG
        📍 Liquidity sweep → OB → FVG
        🎯 Entry: 22450
        🛑 SL: 22380
        🚀 Target: 22600
        🧠 Reason: Retail trapped → Smart money entry
    """
    ordered = graph._topo_sort()

    entry = next((n for n in ordered if n.kind == NodeKind.ENTRY), None)
    sl = next((n for n in ordered if n.kind == NodeKind.STOP_LOSS), None)
    target = next((n for n in ordered if n.kind == NodeKind.TARGET), None)
    confluence = next((n for n in ordered if n.kind == NodeKind.CONFLUENCE), None)

    if not entry:
        return ""

    regime_node = next((n for n in ordered if n.kind == NodeKind.REGIME), None)
    symbol = regime_node.data.get("symbol", "?") if regime_node else "?"
    direction = entry.label.split("@")[0].strip() if "@" in entry.label else entry.data.get("type", "?")
    entry_price = entry.data.get("price", "?")
    sl_price = sl.data.get("price", "?") if sl else "?"
    tgt_price = target.data.get("price", "?") if target else "?"
    rr = target.data.get("rr", "?") if target else "?"
    score = confluence.data.get("total", "?") if confluence else "?"
    grade = confluence.data.get("confidence", "?") if confluence else "?"
    setup = entry.data.get("setup", "?")

    # Build the causal chain summary
    chain_kinds = [n.kind for n in ordered if n.kind not in (
        NodeKind.ENTRY, NodeKind.STOP_LOSS, NodeKind.TARGET,
        NodeKind.OUTCOME, NodeKind.TRAIL_MOVE, NodeKind.CONFLUENCE,
        NodeKind.REGIME, NodeKind.SESSION,
    )]
    chain_labels = {
        NodeKind.LIQUIDITY_SWEEP: "Liquidity sweep",
        NodeKind.DISPLACEMENT: "Displacement",
        NodeKind.CHOCH: "CHoCH",
        NodeKind.BOS: "BOS",
        NodeKind.ORDER_BLOCK: "OB",
        NodeKind.FVG: "FVG",
        NodeKind.HTF_BIAS: "HTF aligned",
        NodeKind.OI_SIGNAL: "OI confirms",
        NodeKind.VOLUME: "Volume expansion",
    }
    chain_str = " → ".join(chain_labels.get(k, k) for k in chain_kinds if k in chain_labels)

    # Build the "Reason" summary
    sweep = next((n for n in ordered if n.kind == NodeKind.LIQUIDITY_SWEEP), None)
    if sweep:
        reason = "Retail trapped → Smart money entry"
    else:
        reason = "Structure shift → Institutional zone entry"

    return (
        f"🔥 <b>{grade} SETUP</b>\n\n"
        f"<b>{symbol}</b> — {direction}\n"
        f"🔮 <b>{setup}</b>\n\n"
        f"📍 {chain_str}\n"
        f"🎯 Entry: {entry_price}\n"
        f"🛑 SL: {sl_price}\n"
        f"🚀 Target: {tgt_price}\n"
        f"📊 RR: {rr} | Score: {score}/10\n\n"
        f"🧠 <b>Reason:</b>\n{reason}"
    )
