"""Telegram report: verified facts first, clearly separated from AI interpretation."""

from __future__ import annotations

import html
from datetime import datetime

from ai_market_agent.models import Story
from ai_market_agent.telegram import split_messages

from .analysis import Interpretation
from .market_data import IST, Quote

DISCLAIMER = (
    "<i>Information only, not investment advice. No predictions or recommendations. "
    "Prices come from an unofficial, delayed public source and may differ from exchange data.</i>"
)


def fmt_quote(q: Quote) -> str:
    name = html.escape(q.name)
    if not q.available or q.change_pct is None or q.close is None:
        return f"• {name}: data unavailable"
    arrow = "🟢" if q.change_pct >= 0 else "🔴"
    return f"{arrow} {name}: {q.close:,.2f} ({q.change_pct:+.2f}%)"


def data_block(indices: list[Quote], sectors: list[Quote], stocks: list[Quote]) -> str:
    """Plain-text facts, also fed to Gemini as its only allowed numbers."""
    lines = []
    for title, group in (("Indices", indices), ("Sectors", sectors), ("Watchlist", stocks)):
        rows = [
            f"{q.name}: close {q.close:,.2f}, change {q.change_pct:+.2f}%"
            for q in group
            if q.available and q.close is not None and q.change_pct is not None
        ]
        if rows:
            lines.append(f"{title} (previous session): " + "; ".join(rows))
    return "\n".join(lines) or "(no market data available)"


def session_date(indices: list[Quote]) -> str | None:
    dates = [q.session for q in indices if q.session]
    return max(dates).isoformat() if dates else None


def format_report(
    indices: list[Quote],
    sectors: list[Quote],
    stocks: list[Quote],
    headlines: list[Story],
    ai: Interpretation | None,
    when: datetime,
    news_ok: bool = True,
) -> list[str]:
    sess = session_date(indices)
    header = f"<b>📈 Indian Market Brief · {when.astimezone(IST):%a %d %b %Y}</b>"
    header += f"\nLast session: {sess}" if sess else "\nLast session: unknown (data unavailable)"

    blocks = ["<b>✅ Verified data</b> (previous close, delayed)\n" + "\n".join(map(fmt_quote, indices))]
    live = [q for q in sectors if q.available and q.change_pct is not None]
    if live:
        live.sort(key=lambda q: q.change_pct or 0, reverse=True)
        blocks.append("<b>Sectors</b>\n" + "\n".join(map(fmt_quote, live)))
    elif sectors:
        blocks.append("<b>Sectors</b>\ndata unavailable")
    if stocks:
        blocks.append("<b>Watchlist</b>\n" + "\n".join(map(fmt_quote, stocks)))

    if headlines:
        rows = []
        for i, s in enumerate(headlines, 1):
            line = f'{i}. <a href="{html.escape(s.url, quote=True)}">{html.escape(s.title[:160])}</a>'
            line += f" <i>({html.escape(s.source)})</i>"
            rows.append(line)
        blocks.append("<b>📰 Headlines</b> (from linked sources)\n" + "\n".join(rows))
    else:
        blocks.append("<b>📰 Headlines</b>\n" + ("none found" if news_ok else "news unavailable"))

    if ai and (ai.market_view or ai.notes):
        part = ["<b>🤖 AI interpretation</b> (unverified, may be wrong)"]
        part += [f"• {html.escape(p)}" for p in ai.market_view]
        for i in sorted(ai.notes):
            part.append(f"↳ [{i}] {html.escape(ai.notes[i])}")
        blocks.append("\n".join(part))
    else:
        blocks.append("<b>🤖 AI interpretation</b>\nunavailable today")

    blocks.append(DISCLAIMER)
    return split_messages(header, blocks)
