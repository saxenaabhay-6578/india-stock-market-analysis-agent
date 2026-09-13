"""
Non-secret runtime configuration for the India stock market analysis agent.

No secrets live here. API keys are read directly from environment
variables (populated from a git-ignored .env file) by the libraries that
need them — e.g. the Anthropic SDK reads ANTHROPIC_API_KEY itself. This
module must never import/expose a secret value.
"""
from __future__ import annotations

from pathlib import Path

# Storage locations
DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "stock_agent.db"
REPORTS_DIR = Path("reports")
LOGS_DIR = Path("logs")

# Universe
TOP_N = 20

# Market data provider
MARKET_DATA_LOOKBACK_DAYS = 95  # ~3 months including weekends/holidays buffer
MARKET_DATA_MAX_RETRIES = 2
MARKET_DATA_BACKOFF_SECONDS = 1.0

# Prediction engine
CLAUDE_MODEL = "claude-sonnet-5"
PROMPT_VERSION = "v1"
