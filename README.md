# AI Market Intelligence Agent

Every day a GitHub Actions job collects fresh AI news from RSS feeds, de-duplicates and ranks it, asks Google Gemini for short summaries, and posts the **top 10 updates** to a Telegram group. No server and no laptop needed; everything runs on free tiers.

## Features

- 10 sources: official blogs (OpenAI, Google DeepMind, Google AI, Hugging Face, NVIDIA, AWS ML) and press (TechCrunch, The Verge, MIT Technology Review, Ars Technica).
- Covers models, launches, research, open source, startups, funding and industry news (keyword relevance + importance scoring).
- De-duplication by canonical URL and by near-identical titles; cross-source coverage raises a story's rank.
- Ranking = importance signals + source trust + recency (24h half-life) + coverage + official-source bonus. Max 3 stories per source for variety.
- Titles, sources, dates and links come **only from the feeds**. Gemini writes summaries from the feed text and is told not to add facts; it never supplies links or dates. If it skips a story, that story is dropped rather than sent without a summary.
- Fewer than 10 stories? It sends fewer.
- Retries with exponential backoff for timeouts, 5xx and rate limits; no retries for permanent errors (bad key, wrong chat, daily quota).
- Failure message to the Telegram group + non-zero exit code (red run in GitHub); success confirmation message after delivery.
- Remembers sent URLs for 14 days (Actions cache) so stories are not repeated.

## Architecture and flow

```
RSS feeds ─► sources.py (fetch, retry, parse, normalise, 36h window)
          ─► state.py   (drop URLs already sent)
          ─► ranking.py (relevance filter, dedupe, score, per-source cap, top N)
          ─► gemini.py  (one JSON request: summaries for the N stories)
          ─► telegram.py(HTML format, split to <4096 chars, send)
          ─► success note + save sent URLs   (any failure ─► failure note, exit 1)
```

Code lives in `src/ai_market_agent/`; tests in `tests/`; workflows in `.github/workflows/`.

## Prerequisites

- A GitHub account and a repository for this project.
- A free Gemini API key: <https://aistudio.google.com/apikey>.
- A Telegram account.
- For local runs/tests: Python 3.11+ (CI uses 3.12).

## Telegram bot and group setup

1. In Telegram, message **@BotFather** → `/newbot` → follow prompts → copy the **bot token** (keep it private).
2. Create a group (or use an existing one) and **add your bot** as a member.
3. Send any message in the group (e.g. `hello`).
4. Find the chat id: open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser (replace `<YOUR_TOKEN>`), and look for `"chat":{"id":-100...}`. Group ids are negative; supergroups start with `-100`. If `result` is empty, send another message in the group and reload.
5. If posting later fails with 403, make the bot a group admin.

## Environment variables

| Name | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | yes | Gemini API key |
| `GEMINI_MODEL` | yes | Name of the Gemini model to use. No default: pick one that your key can access in [Google AI Studio](https://aistudio.google.com) and set it yourself (models get renamed and retired, so nothing is hardcoded) |
| `TELEGRAM_BOT_TOKEN` | yes | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | yes | Target group id |
| `MAX_STORIES` | no | 1-25, default 10 |
| `LOOKBACK_HOURS` | no | 1-168, default 36 |

Only the project-folder `.env` file is loaded locally; real environment variables take precedence over it.

## Local setup

```bash
python3 -m venv .venv            # on Debian/Ubuntu: sudo apt install python3-venv (or use virtualenv)
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env             # then edit it with your real values (never commit it)

pytest                           # tests use mocks, no credentials needed
ruff check . && mypy src         # lint + types

PYTHONPATH=src python -m ai_market_agent --dry-run   # real news + real Gemini, prints digest, sends nothing (needs only GEMINI_API_KEY and GEMINI_MODEL)
PYTHONPATH=src python -m ai_market_agent             # full run, posts to Telegram
```

Exit codes: `0` ok, `1` run failed, `2` configuration problem.

## GitHub Secrets setup

Repository → **Settings → Secrets and variables → Actions → New repository secret**. Create three secrets with exactly these names: `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Never put them in code, workflow files or docs.

The model name is not secret, so set it as a repository **variable** instead: same page → **Variables** tab → **New repository variable** → name `GEMINI_MODEL`, value = your chosen model.

## GitHub Actions setup and manual run

1. Push this project to a GitHub repo (default branch `main`). Scheduled workflows only run from the default branch.
2. Add the three secrets and the `GEMINI_MODEL` variable (above).
3. Go to **Actions → Daily AI Digest → Run workflow** to test immediately. Check the Telegram group and the run log.
4. The `CI` workflow runs lint, types and tests on pushes/PRs and needs no secrets.

Note: GitHub disables scheduled workflows after 60 days without repository activity on public repos; re-enable under the Actions tab, or run it manually.

## Daily scheduling

The schedule is one line in [.github/workflows/daily-digest.yml](.github/workflows/daily-digest.yml):

```yaml
- cron: "0 7 * * *"   # minute hour day month weekday, in UTC
```

Default: **07:00 UTC daily** (12:30 IST, 08:00 CET in winter, 03:00 US Eastern in summer). To change it, edit the minute and hour in UTC, e.g. `"30 2 * * *"` = 02:30 UTC. GitHub may delay scheduled runs by several minutes (more at busy times such as the top of the hour); pick an off-peak minute like `17` if punctuality matters.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Exit 2, "Missing required configuration" (incl. `GEMINI_MODEL`) | A variable/secret is unset, empty or still a placeholder (`your-...`). Fix your local env file or the GitHub secrets. |
| `Gemini rejected the API key` | Key invalid/revoked. Create a new one at AI Studio and update `GEMINI_API_KEY`. Not retried. |
| `Gemini daily free-tier quota exhausted` | Free daily request limit hit. Resets daily; re-run later or point `GEMINI_MODEL` at a different model. The agent uses 1 Gemini request per run, so this usually means the key is shared with other apps. |
| `Gemini rate limit (429)` | Per-minute limit; retried automatically with backoff, then fails if it persists. |
| `Gemini model '...' not found` | Your `GEMINI_MODEL` value is wrong or retired. Set it to a model listed in AI Studio. |
| Telegram `HTTP 403` | Bot not in the group or was removed. Re-add it (make it admin if needed). |
| Telegram `HTTP 400 ... chat not found` | Wrong `TELEGRAM_CHAT_ID`. Re-check via `getUpdates`; a group upgraded to a supergroup gets a new `-100...` id. |
| Telegram `HTTP 401/404` | Bad `TELEGRAM_BOT_TOKEN`. |
| "All news feeds failed" | Network/DNS problem or every feed URL changed. Individual feed failures are only warnings. |
| "no new relevant AI stories" message | Nothing new in the window (or all already sent). Normal on very quiet days. |
| Same story repeated | The sent-history cache was evicted (7 days unused) or cleared; harmless. |

On failure the group receives a message with the failing stage and reason (never secrets); full diagnostics are in the Actions log.

## Costs and free-tier limits

- GitHub Actions: free for public repos; private repos get a monthly free-minute quota (a run takes about a minute).
- Gemini API: free tier, with rate and daily request limits that Google can change. One run makes one request (a few thousand tokens). On the free tier Google may use submitted content to improve its products; see their terms.
- Telegram Bot API: free.
- RSS feeds: free; some outlets rate-limit or block cloud IPs, and feeds can change URL. The agent skips failing feeds.
