"""
content_engine/services/formatter.py

Converts raw agent output into Telegram-ready Markdown messages.

Rules:
  - Telegram uses MarkdownV2 — special chars must be escaped
  - Keep messages under 4096 chars (Telegram limit)
  - Each formatter returns a plain string ready to send
"""

import textwrap
from datetime import datetime

# Characters that must be escaped in Telegram MarkdownV2
_ESCAPE_CHARS = r"\_*[]()~`>#+-=|{}.!"


def escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    for ch in _ESCAPE_CHARS:
        text = text.replace(ch, f"\\{ch}")
    return text


def _truncate(text: str, max_len: int = 4000) -> str:
    """Trim message to stay within Telegram's 4096 char limit."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n\\.\\.\\.\\(truncated\\)"


# ── Strategy Post ─────────────────────────────────────────────────────────────

def format_strategy_post(
    analysis: str,
    prices: dict[str, float],
    date: datetime | None = None,
) -> str:
    """
    Format the daily strategy post.

    Args:
        analysis: AI-generated text from strategy_agent
        prices:   {symbol: current_price} from scraper
        date:     post date (defaults to today)
    """
    date = date or datetime.now()
    date_str = escape_md(date.strftime("%d %b %Y, %A"))

    lines = [
        f"📊 *Daily Market Strategy — {date_str}*",
        "",
    ]

    # Index price summary table
    if prices:
        lines.append("*Index Snapshot*")
        for symbol, price in prices.items():
            sym_e = escape_md(symbol)
            price_e = escape_md(f"{price:,.2f}")
            lines.append(f"  • {sym_e}: `{price_e}`")
        lines.append("")

    # Main AI analysis block
    lines.append("*Strategy Insight*")
    lines.append(escape_md(analysis))
    lines.append("")
    lines.append(escape_md("— SMC Trading Engine | Content AI"))

    message = "\n".join(lines)
    return _truncate(message)


# ── News Digest Post ──────────────────────────────────────────────────────────

def format_news_digest(
    articles: list[dict],
    summary: str,
    date: datetime | None = None,
) -> str:
    """
    Format the daily news digest post.

    Args:
        articles: list of {title, url, source, description}
        summary:  AI-generated summary of key themes
        date:     post date (defaults to today)
    """
    date = date or datetime.now()
    date_str = escape_md(date.strftime("%d %b %Y"))

    lines = [
        f"📰 *Market News Digest — {date_str}*",
        "",
    ]

    if summary:
        lines.append("*AI Summary*")
        lines.append(escape_md(summary))
        lines.append("")

    if articles:
        lines.append("*Top Stories*")
        for i, article in enumerate(articles[:6], start=1):
            title = escape_md(article.get("title", "Untitled")[:80])
            url = article.get("url", "")
            source = escape_md(article.get("source", ""))
            lines.append(f"{i}\\. [{title}]({url}) _{source}_")
        lines.append("")

    lines.append(escape_md("— SMC Trading Engine | Content AI"))

    message = "\n".join(lines)
    return _truncate(message)


# ── Error / Alert Post ────────────────────────────────────────────────────────

def format_error_alert(agent_name: str, error: str) -> str:
    """Simple error notification for ops channel."""
    agent_e = escape_md(agent_name)
    error_e = escape_md(str(error)[:300])
    return f"⚠️ *Content Engine Error*\n\nAgent: `{agent_e}`\nError: {error_e}"
