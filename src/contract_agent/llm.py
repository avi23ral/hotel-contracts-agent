"""Claude client setup.

Key lookup order: HOTEL_AGENT_API_KEY, then ANTHROPIC_API_KEY.
Both can come from the environment or from a local .env file (never committed).
"""
from __future__ import annotations

import os
from pathlib import Path

import anthropic

KEY_VARS = ("HOTEL_AGENT_API_KEY", "ANTHROPIC_API_KEY")
ROOT = Path(__file__).resolve().parents[2]

# Model choice is a config value so evals can compare models (cost vs quality).
EXTRACT_MODEL = os.getenv("HOTEL_AGENT_MODEL", "claude-opus-5-5")
FAST_MODEL = os.getenv("HOTEL_AGENT_FAST_MODEL", "claude-haiku-5-5")  # for intent detection (step 4)


def _load_dotenv(path: Path = ROOT / ".env") -> dict[str, str]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def resolve_api_key(env: dict[str, str] | None = None, dotenv: dict[str, str] | None = None) -> str | None:
    env = dict(os.environ) if env is None else env
    dotenv = _load_dotenv() if dotenv is None else dotenv
    for var in KEY_VARS:
        for source in (env, dotenv):
            if source.get(var):
                return source[var]
    return None


def get_client() -> anthropic.Anthropic:
    key = resolve_api_key()
    if not key:
        raise RuntimeError(
            "No API key found. Set HOTEL_AGENT_API_KEY (or ANTHROPIC_API_KEY) "
            "in your environment or in a .env file at the repo root."
        )
    return anthropic.Anthropic(api_key=key)
