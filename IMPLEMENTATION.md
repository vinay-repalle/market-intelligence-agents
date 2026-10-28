# Implementation Tracker

## Requirements (summary)
Daily top-10 AI industry digest → Telegram group. Python, Gemini (summaries only), RSS sources, Telegram Bot API, GitHub Actions scheduling, free to run, secrets only via env/GitHub Secrets, no fabricated stories/links/dates.

## Architecture decisions
- **Gemini only summarises.** Ranking is deterministic and heuristic (testable, free, no extra quota). Titles/links/dates come from RSS, so Gemini cannot invent them. One Gemini request per run (JSON output; ids validated; unknown ids ignored; stories without a summary are dropped).
- **Gemini via REST (`requests`)**, key sent in the `x-goog-api-key` header (never in URLs/logs). Avoids a heavy SDK dependency.
- **Telegram HTML parse mode** with `html.escape` on all dynamic text; messages split on story boundaries (limit 3800 chars) so tags are never cut.
- **Error taxonomy** (`errors.py`): `TransientError` is retried with exponential backoff + jitter and honours `Retry-After`; `PermanentError` subclasses (bad key, daily quota, wrong chat) fail immediately.
- **Network exception text is never logged** (it can embed the bot token in the URL); a redacting log filter is a second line of defence.
- **Duplicate prevention across days:** `.agent_state/sent.json` persisted through `actions/cache` (no write permission or commits needed). Written only after successful delivery.
- **Undated items are excluded** (recency cannot be verified); items dated more than 1h in the future are excluded.
- **No Gemini model is hardcoded.** `GEMINI_MODEL` is a required setting (env var locally, repository variable `GEMINI_MODEL` in Actions); if missing or still the placeholder, the run exits with code 2 and an explanation. A test asserts no `gemini-*` model string appears in `src/`.
- Only the project-folder `.env` is loaded (no parent-directory search).
- Dependencies pinned in `requirements.txt` for reproducible installs.

## Stages
| # | Stage | Status |
|---|---|---|
| 1 | Inspect project/Git (empty repo, no commits, no remote) | Done |
| 2 | Scaffolding: `.gitignore`, `.env.example`, `.claude/settings.json`, `pyproject.toml`, pinned requirements | Done |
| 3 | Config + logging + redaction + retry | Done |
| 4 | RSS collection/normalisation | Done |
| 5 | Dedupe + ranking | Done |
| 6 | Gemini summarisation + error handling | Done |
| 7 | Telegram formatting/splitting/delivery | Done |
| 8 | Orchestration, failure/success notifications, state | Done |
| 9 | GitHub Actions (daily + manual, CI) | Done (not yet run on GitHub) |
| 10 | Tests, lint, types | Done |
| 11 | README / IMPLEMENTATION | Done |
| 12 | First live run with real credentials on GitHub | **Pending (user)** |

## Tests executed (local, Python 3.12.3)
- `pytest -q`: **89 passed** at last run (sources/normalisation, ranking/dedupe, Gemini parsing and errors, Telegram formatting/splitting/errors, config, retry, state, orchestration, workflow/repo-hygiene checks). All external APIs mocked; no credentials used.
- `ruff check .`: all checks passed. `mypy src` (strict): no issues.
- Live feed smoke test (no credentials): 9 of 11 feeds responded then (Ars Technica had SSL errors from this machine; VentureBeat returned 429 and was removed from the list); 37 recent items collected and ranked into a plausible top 10.
- CLI with missing configuration: exits with code 2 and setup instructions.
- One real `--dry-run` (loaded the user's local `.env`, contents never viewed): 9 feeds, 37 items, 10 stories selected, Gemini summaries returned and the digest printed, exit 0. Nothing was sent to Telegram.
- **Not run:** a real Telegram send and the GitHub workflows (need the bot/chat id and the repo).

## Known limitations
- Importance is keyword-based and can mis-rank unusual stories. Summaries rely on short RSS excerpts, so some are brief.
- Anthropic and Meta publish no official RSS feeds, so they are covered only through press coverage.
- Feed URLs can change or block cloud IPs; failed feeds are skipped with a warning (Ars Technica may fail the same way on GitHub runners).
- Gemini free-tier limits and model names change over time (the user must choose a valid `GEMINI_MODEL`; it was never exercised against the live API here).
- Sent-history cache is best-effort (GitHub evicts caches unused for 7 days).
- Scheduled runs can be delayed by GitHub; public repos pause schedules after 60 days of inactivity.
- The success message is separate from the digest; if it fails only a warning is logged.

## Final launch checklist
- [ ] Create bot with @BotFather; add it to the group; get the chat id (README).
- [ ] Put real values (including `GEMINI_MODEL`) in the local `.env` (git-ignored); `TELEGRAM_CHAT_ID` was still unset or a placeholder at last check.
- [ ] `PYTHONPATH=src python -m ai_market_agent --dry-run` prints a digest.
- [ ] Create the GitHub repo, review `git status` (no `.env`), commit and push.
- [ ] Add secrets `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and repository variable `GEMINI_MODEL`.
- [ ] Run **Daily AI Digest** manually; confirm the digest and the ✅ message in Telegram.
- [ ] Adjust the cron line if 07:00 UTC is not what you want.
