"use client";
/**
 * /chat - AI Chatbot Page
 * Powered by GPT-4o via POST /api/chat (SSE streaming)
 * System prompt includes live engine state, today's trades, rolling stats
 */
import { useState, useRef, useEffect, useCallback } from "react";
import { Send, Bot, User, Sparkles, RefreshCw, Settings } from "lucide-react";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

interface Message {
  id:        number;
  role:      "user" | "assistant";
  content:   string;
  ts:        string;
  streaming?: boolean;
}

const STARTERS = [
  "How is my P&L today?",
  "Which setup has the best win rate?",
  "Am I in any active trades?",
  "What is the current market regime?",
  "Show me my last 5 trades",
  "Is my system still profitable?",
  "Are there any risk alerts I should know about?",
];

let _id = 0;
const uid = () => ++_id;

// -- HTML sanitizer (strips dangerous tags/attributes) -------------------------
function sanitizeHtml(html: string): string {
  let clean = html.replace(/<(script|iframe|object|embed|form|style)\b[^>]*>[\s\S]*?<\/\1>/gi, "");
  clean = clean.replace(/<(script|iframe|object|embed|form|style)\b[^>]*\/?>/gi, "");
  clean = clean.replace(/\s+on\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "");
  clean = clean.replace(/href\s*=\s*["']?\s*javascript:/gi, 'href="');
  return clean;
}

// -- Simple markdown renderer --------------------------------------------------
function renderMd(text: string): string {
  const html = text
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.*?)\*/g,     "<em>$1</em>")
    .replace(/`([^`]+)`/g,     "<code>$1</code>")
    .replace(/^###\s(.+)$/gm,  "<h4>$1</h4>")
    .replace(/^##\s(.+)$/gm,   "<h3>$1</h3>")
    .replace(/^#\s(.+)$/gm,    "<h2>$1</h2>")
    .replace(/^[-\u2022]\s(.+)$/gm, "<li>$1</li>")
    .replace(/(<li>.*<\/li>)/g, "<ul>$1</ul>")
    .replace(/\n{2,}/g, "<br/><br/>")
    .replace(/\n/g, "<br/>");
  return sanitizeHtml(html);
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id:      uid(),
      role:    "assistant",
      content: "Hello! I'm your SMC Trading Assistant powered by GPT-3.5.\n\nI have live access to your **engine state**, **today's trades**, **rolling P&L**, **active positions**, and **agent logs**.\n\nAsk me anything about your trading system.",
      ts:      "",
    },
  ]);

  // Set the welcome message timestamp on the client only (avoids hydration mismatch)
  useEffect(() => {
    setMessages(prev =>
      prev.map((m, i) => i === 0 && !m.ts ? { ...m, ts: new Date().toLocaleTimeString("en-IN") } : m)
    );
  }, []);

  const [input,     setInput]    = useState("");
  const [loading,   setLoading]  = useState(false);
  const [apiKeySet, setApiKeySet]= useState<boolean | null>(null);
  const endRef     = useRef<HTMLDivElement>(null);
  const inputRef   = useRef<HTMLInputElement>(null);
  const abortRef   = useRef<AbortController | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Check if API key is configured
  useEffect(() => {
    fetch(`${BASE}/api/chat/context`)
      .then(r => r.ok ? r.json() : null)
      .then(d => setApiKeySet(d !== null))
      .catch(() => setApiKeySet(false));
  }, []);

  const send = useCallback(async (text?: string) => {
    const q = (text ?? input).trim();
    if (!q || loading) return;
    setInput("");
    inputRef.current?.focus();

    const userMsg: Message = {
      id:      uid(),
      role:    "user",
      content: q,
      ts:      new Date().toLocaleTimeString("en-IN"),
    };

    const assistantId = uid();
    const assistantMsg: Message = {
      id:        assistantId,
      role:      "assistant",
      content:   "",
      ts:        new Date().toLocaleTimeString("en-IN"),
      streaming: true,
    };

    setMessages(prev => [...prev, userMsg, assistantMsg]);
    setLoading(true);

    // Build history - truncate to last 20 messages to stay within context window
    const MAX_HISTORY = 20;
    const fullHistory = [...messages, userMsg].map(m => ({
      role:    m.role,
      content: m.content,
    }));
    const history = fullHistory.slice(-MAX_HISTORY);

    try {
      abortRef.current?.abort();
      abortRef.current = new AbortController();

      const resp = await fetch(`${BASE}/api/chat`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ messages: history }),
        signal:  abortRef.current.signal,
      });

      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        throw new Error((detail as { detail?: string }).detail || `HTTP ${resp.status}`);
      }

      const reader = resp.body!.getReader();
      const dec    = new TextDecoder();
      let   buf    = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });

        const lines = buf.split("\n");
        buf = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (raw === "[DONE]") break;

          try {
            const payload = JSON.parse(raw) as { delta?: string; error?: string };
            if (payload.error) {
              setMessages(prev => prev.map(m =>
                m.id === assistantId
                  ? { ...m, content: `\u26A0\uFE0F ${payload.error}`, streaming: false }
                  : m
              ));
              break;
            }
            if (payload.delta) {
              setMessages(prev => prev.map(m =>
                m.id === assistantId
                  ? { ...m, content: m.content + payload.delta }
                  : m
              ));
            }
          } catch { /* ignore parse errors */ }
        }
      }
    } catch (err: unknown) {
      if ((err as Error).name === "AbortError") return;
      const msg = err instanceof Error ? err.message : "Unknown error";
      setMessages(prev => prev.map(m =>
        m.id === assistantId
          ? { ...m, content: `\u26A0\uFE0F ${msg}`, streaming: false }
          : m
      ));
    } finally {
      setMessages(prev => prev.map(m =>
        m.id === assistantId ? { ...m, streaming: false } : m
      ));
      setLoading(false);
    }
  }, [input, loading, messages]);

  const stopStream = () => {
    abortRef.current?.abort();
    setLoading(false);
    setMessages(prev => prev.map(m => m.streaming ? { ...m, streaming: false } : m));
  };

  const clearChat = () => {
    setMessages([{
      id:      uid(),
      role:    "assistant",
      content: "Chat cleared. I still have live access to your engine state. How can I help?",
      ts:      new Date().toLocaleTimeString("en-IN"),
    }]);
    setLoading(false);
    abortRef.current?.abort();
  };

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 16, height: "calc(100vh - 120px)" }}>

      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <h1 className="text-xl md:text-2xl lg:text-3xl font-bold m-0">AI Assistant</h1>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.8rem", margin: "3px 0 0" }}>
            GPT-3.5 &middot; Live engine context &middot; Streaming
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {apiKeySet === true  && <span className="badge badge-live"    style={{ fontSize: "0.72rem" }}><Sparkles size={10}/> GPT-3.5</span>}
          {apiKeySet === false && <span className="badge badge-warning" style={{ fontSize: "0.72rem" }}><Settings size={10}/> No API Key</span>}
          <button onClick={clearChat}
            style={{ padding: "5px 12px", borderRadius: 6, fontSize: "0.74rem", cursor: "pointer",
              background: "rgba(255,255,255,0.05)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
            Clear
          </button>
        </div>
      </div>

      {/* API key warning */}
      {apiKeySet === false && (
        <div className="glass" style={{ padding: "12px 18px", color: "var(--warning)", fontSize: "0.82rem",
          display: "flex", gap: 10, alignItems: "center", borderLeft: "3px solid var(--warning)" }}>
          <Settings size={14} />
          <div>
            <strong>OPENAI_API_KEY not configured.</strong> Set it as an environment variable before starting the backend.
            <div style={{ fontSize: "0.73rem", color: "var(--text-secondary)", marginTop: 3 }}>
              Windows: <code>$env:OPENAI_API_KEY=&quot;sk-...&quot;</code> then restart the backend.
            </div>
          </div>
        </div>
      )}

      <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>

        {/* Messages */}
        <div className="glass" style={{
          flex: 1, overflowY: "auto", padding: "16px",
          display: "flex", flexDirection: "column", gap: 14, minHeight: 0,
        }}>
          {messages.map(m => <ChatBubble key={m.id} msg={m} />)}

          {loading && !messages.at(-1)?.streaming && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--text-secondary)", fontSize: "0.82rem" }}>
              <Bot size={16} color="var(--accent)" />
              <ThinkingDots />
            </div>
          )}
          <div ref={endRef} />
        </div>

        {/* Starters */}
        <div style={{ display: "flex", gap: 6, padding: "10px 0", overflowX: "auto", scrollbarWidth: "none" }}>
          {STARTERS.map(s => (
            <button key={s} onClick={() => send(s)} disabled={loading}
              style={{
                whiteSpace: "nowrap", padding: "5px 12px", borderRadius: 20,
                background: "var(--accent-dim)", border: "1px solid rgba(0,212,255,0.2)",
                color: "var(--accent)", fontSize: "0.74rem", cursor: loading ? "not-allowed" : "pointer",
                opacity: loading ? 0.5 : 1,
              }}>
              {s}
            </button>
          ))}
        </div>

        {/* Input bar */}
        <div className="glass" style={{ display: "flex", gap: 8, padding: "10px 12px", alignItems: "center" }}>
          <input
            ref={inputRef}
            className="input-dark"
            style={{ flex: 1 }}
            placeholder="Ask about your trades, P&L, regime, risk..."
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
            disabled={loading}
          />
          {loading ? (
            <button onClick={stopStream}
              style={{ padding: "7px 14px", borderRadius: 6, background: "var(--danger)", border: "none",
                color: "#fff", cursor: "pointer", display: "flex", alignItems: "center", gap: 5, fontSize: "0.78rem" }}>
              <RefreshCw size={13} style={{ animation: "spin 1s linear infinite" }} /> Stop
            </button>
          ) : (
            <button className="btn-accent" onClick={() => send()}
              disabled={!input.trim()}
              style={{ padding: "7px 14px", opacity: !input.trim() ? 0.4 : 1 }}>
              <Send size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Context note */}
      <div style={{
        padding: "9px 14px", borderRadius: 8,
        background: "rgba(0,212,255,0.04)", border: "1px solid rgba(0,212,255,0.12)",
        fontSize: "0.73rem", color: "var(--text-secondary)", display: "flex", gap: 8, alignItems: "center",
      }}>
        <Sparkles size={11} color="var(--accent)" style={{ flexShrink: 0 }} />
        Context injected on every message: engine state &middot; today&apos;s trades &middot; rolling 20-trade stats &middot; active positions &middot; recent agent runs
        &middot; debug: <a href={`${BASE}/api/chat/context`} target="_blank" rel="noreferrer"
          style={{ color: "var(--accent)", textDecoration: "none" }}>/api/chat/context</a>
      </div>
    </div>
  );
}

// -- Chat bubble ---------------------------------------------------------------
function ChatBubble({ msg }: { msg: Message }) {
  const isUser = msg.role === "user";
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "flex-start", flexDirection: isUser ? "row-reverse" : "row" }}>
      {/* Avatar */}
      <div style={{
        width: 28, height: 28, borderRadius: "50%", flexShrink: 0,
        background: isUser ? "rgba(0,212,255,0.15)" : "rgba(0,224,150,0.10)",
        border: `1px solid ${isUser ? "rgba(0,212,255,0.3)" : "rgba(0,224,150,0.2)"}`,
        display: "flex", alignItems: "center", justifyContent: "center",
      }}>
        {isUser ? <User size={13} color="var(--accent)" /> : <Bot size={13} color="var(--success)" />}
      </div>

      {/* Bubble */}
      <div style={{
        maxWidth: "78%", padding: "10px 14px",
        borderRadius: isUser ? "12px 4px 12px 12px" : "4px 12px 12px 12px",
        background: isUser ? "var(--accent-dim)" : "var(--bg-card)",
        border: `1px solid ${isUser ? "rgba(0,212,255,0.2)" : "var(--border)"}`,
        fontSize: "0.84rem", lineHeight: 1.6, color: "var(--text-primary)",
      }}>
        {isUser ? (
          <span style={{ whiteSpace: "pre-wrap" }}>{msg.content}</span>
        ) : (
          <div
            dangerouslySetInnerHTML={{ __html: renderMd(msg.content) }}
            style={{ whiteSpace: "pre-wrap" }}
          />
        )}
        {msg.streaming && <BlinkCursor />}
        <div style={{ fontSize: "0.63rem", color: "var(--text-dim)", marginTop: 5, textAlign: isUser ? "right" : "left" }}>
          {msg.ts}{msg.streaming ? " \u00B7 streaming\u2026" : ""}
        </div>
      </div>
    </div>
  );
}

function BlinkCursor() {
  return (
    <span style={{
      display: "inline-block", width: 2, height: "1em",
      background: "var(--accent)", marginLeft: 2, verticalAlign: "text-bottom",
      animation: "blink 1s step-end infinite",
    }} />
  );
}

function ThinkingDots() {
  return (
    <span style={{ letterSpacing: 3 }}>
      <span style={{ animation: "blink 1.2s step-end infinite" }}>{"\u25CF"}</span>
      <span style={{ animation: "blink 1.2s step-end 0.4s infinite" }}>{"\u25CF"}</span>
      <span style={{ animation: "blink 1.2s step-end 0.8s infinite" }}>{"\u25CF"}</span>
    </span>
  );
}
