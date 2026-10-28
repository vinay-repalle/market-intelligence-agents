"""Static checks on the GitHub Actions workflow and repo hygiene (no YAML dependency)."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = (ROOT / ".github/workflows/daily-digest.yml").read_text()


def test_has_schedule_and_manual_trigger() -> None:
    assert re.search(r'^\s*- cron: "\d+ \d+ \* \* \*"', WF, re.M)
    assert "workflow_dispatch:" in WF


def test_uses_required_secret_names_only_via_secrets_context() -> None:
    for name in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        assert f"{name}: ${{{{ secrets.{name} }}}}" in WF


def test_no_hardcoded_secrets_anywhere() -> None:
    pattern = re.compile(r"AIza[0-9A-Za-z_-]{20,}|\d{8,}:[A-Za-z0-9_-]{30,}")
    for path in ROOT.rglob("*"):
        if path.is_file() and not any(
            p in path.parts for p in (".git", ".venv", ".mypy_cache", ".ruff_cache", ".pytest_cache", "__pycache__")
        ):
            if path.name == ".env":
                continue
            assert not pattern.search(path.read_text(errors="ignore")), path


def test_workflow_passes_model_from_repository_variable() -> None:
    assert "GEMINI_MODEL: ${{ vars.GEMINI_MODEL }}" in WF


def test_workflow_uses_supported_python_and_least_privilege() -> None:
    assert 'python-version: "3.12"' in WF
    assert "contents: read" in WF


def test_gitignore_protects_env_but_not_example() -> None:
    gi = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in gi and ".env.*" in gi and "!.env.example" in gi and ".venv/" in gi


def test_env_example_has_placeholders_only() -> None:
    text = (ROOT / ".env.example").read_text()
    for name in ("GEMINI_API_KEY", "GEMINI_MODEL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        assert re.search(rf"^{name}=your-", text, re.M)


def test_claude_settings_deny_env_files() -> None:
    deny = json.loads((ROOT / ".claude/settings.json").read_text())["permissions"]["deny"]
    assert "Read(./.env)" in deny and "Edit(./.env)" in deny and "Read(./.env.*)" in deny


def test_requirements_are_pinned() -> None:
    for line in (ROOT / "requirements.txt").read_text().split():
        assert "==" in line


STOCK_WF = (ROOT / ".github/workflows/stock-report.yml").read_text()


def test_stock_workflow_is_independent_and_runs_0700_ist_weekdays() -> None:
    assert '- cron: "30 1 * * 1-5"' in STOCK_WF  # 01:30 UTC = 07:00 IST
    assert "workflow_dispatch:" in STOCK_WF
    assert "group: stock-report" in STOCK_WF and "group: daily-digest" not in STOCK_WF
    assert "TELEGRAM_STOCK_CHAT_ID: ${{ secrets.TELEGRAM_STOCK_CHAT_ID }}" in STOCK_WF
    assert "TELEGRAM_CHAT_ID: ${{" not in STOCK_WF
    assert "stock-state-" in STOCK_WF and "sent-state-" not in STOCK_WF


def test_news_schedule_unchanged() -> None:
    assert '- cron: "0 7 * * *"' in WF  # 12:30 IST
