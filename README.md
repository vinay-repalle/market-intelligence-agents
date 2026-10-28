# Market Intelligence Agents

Two independent agents in one repository, each run by its own GitHub Actions workflow and posting to its own Telegram group through the same bot. No server needed; everything runs on free tiers.

| Agent | Package | Workflow | Schedule (default) | Telegram group |
|---|---|---|---|---|
| **AI News Agent** | `src/ai_market_agent/` | `daily-digest.yml` | 12:30 PM IST daily | `TELEGRAM_CHAT_ID` |
| **Stock Market Agent** | `src/stock_agent/` | `stock-report.yml` | 7:00 AM IST, Mon-Fri | `TELEGRAM_STOCK_CHAT_ID` |

The stock agent reuses the news agent's retry, error, logging, RSS and Telegram code, but has its own config, state and workflow, so a failure in one never affects the other.

## AI News Agent

Collects AI news from 10 RSS feeds, de-duplicates and ranks it, asks Gemini for short summaries and posts the top stories. Each story is a **headline**, up to **three short bullets** (what happened, why it matters, what's next) and a **source link**. Titles, links and dates come only from the feeds; Gemini cannot add facts, and stories it skips are dropped. Sent URLs are remembered for 14 days (Actions cache).

## Stock Market Agent (India)

Posts a concise 7:00 AM IST brief using the previous trading session:

- **Verified data:** NIFTY 50, SENSEX, BANK NIFTY, sector indices (IT, Auto, Pharma, FMCG, Metal, Energy, Realty) and an optional watchlist. Prices come from Yahoo Finance's public chart endpoint, which is **unofficial and delayed**; every report says so. Anything missing is shown as "data unavailable", never estimated.
- **Headlines:** relevant market, economy, results and RBI/SEBI news from Economic Times, Moneycontrol, Mint and Business Standard RSS, each with a link.
- **AI interpretation:** a separate, labelled "unverified" section. Gemini only sees the data above, and any sentence quoting a number not in that data is discarded. No predictions, targets or trade advice. If Gemini is down, the verified report is still sent.
- **No duplicates:** one report per trading session (`.stock_state/`), so reruns and market holidays do not repeat it.

Limits: this is not an exchange feed, so there are no live or intraday prices and no official corporate-announcement feed. The price source sits in `market_data.py` and can be swapped for a broker API later.

## Setup

Requirements: Python 3.11+, a free [Gemini API key](https://aistudio.google.com/apikey), and a Telegram bot.

**Telegram:** message **@BotFather** → `/newbot` and copy the token. Create one group per agent and add the bot to both. Send a message in each group, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `"chat":{"id":-100...}`. If posting fails with 403, make the bot an admin.

**Environment variables** (copy `.env.example` to `.env` locally; never commit it):

| Name | Used by | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | both | Gemini key |
| `GEMINI_MODEL` | both | Gemini model name (no default; pick one your key can use) |
| `TELEGRAM_BOT_TOKEN` | both | Bot token |
| `TELEGRAM_CHAT_ID` | news | News group id |
| `TELEGRAM_STOCK_CHAT_ID` | stock | Stock group id |
| `STOCK_WATCHLIST` | stock, optional | NSE symbols, e.g. `RELIANCE,TCS` (max 15; `.NS` added automatically, `.BO` for BSE) |
| `STOCK_LOOKBACK_HOURS`, `STOCK_MAX_HEADLINES` | stock, optional | News window (default 30h) and headline count (default 8) |
| `MAX_STORIES`, `LOOKBACK_HOURS` | news, optional | Defaults 10 and 36 |

**GitHub (Settings → Secrets and variables → Actions):**
- Secrets: `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_STOCK_CHAT_ID`
- Variables: `GEMINI_MODEL`, and optionally `STOCK_WATCHLIST`

## GitHub Actions schedules

Each schedule is one cron line (UTC) in its workflow file:

```yaml
# .github/workflows/daily-digest.yml  - news, 12:30 IST daily
- cron: "0 7 * * *"
# .github/workflows/stock-report.yml  - stocks, 07:00 IST Mon-Fri
- cron: "30 1 * * 1-5"
```

IST = UTC + 5:30. Run either workflow manually from **Actions → Run workflow**. GitHub can delay scheduled runs by several minutes, and disables schedules after 60 days of repository inactivity (re-enable in the Actions tab). The `CI` workflow runs Ruff, mypy and pytest on pushes and PRs.

## Local development and testing

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

pytest -q                 # all tests are mocked; no credentials needed
ruff check . && mypy src  # lint and strict types

# Dry runs print the message and send nothing (need only GEMINI_API_KEY and GEMINI_MODEL)
PYTHONPATH=src python -m ai_market_agent --dry-run
PYTHONPATH=src python -m stock_agent --dry-run
PYTHONPATH=src python -m stock_agent --force   # resend even if today's report was already sent
```

Exit codes: `0` ok, `1` run failed (a failure note is sent to that agent's group), `2` configuration problem.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Exit 2, "Missing required configuration" | A secret or variable is unset or still a placeholder. |
| `Gemini rejected the API key` | Create a new key and update `GEMINI_API_KEY`. |
| `Gemini daily free-tier quota exhausted` | Resets daily. The stock report still sends its verified data without AI text. |
| `Gemini model '...' not found` | Set `GEMINI_MODEL` to a model listed in AI Studio. |
| Telegram `403` / `400 chat not found` | Bot not in the group, or wrong chat id (supergroup ids start with `-100`). |
| "data unavailable" in the stock report | Yahoo rate-limited or changed its endpoint; the report says so rather than guessing. |
| No stock report on a market holiday | Expected: there was no new session, so nothing new to report. |
| Stock report repeated | The `stock-state-` Actions cache was evicted; harmless. |

## Costs

GitHub Actions is free for public repos (private repos have a monthly quota; each run takes about a minute). Gemini uses its free tier at one request per run per agent. Telegram, RSS and the Yahoo endpoint are free, but the price endpoint is unofficial and carries no uptime or licensing guarantee.
