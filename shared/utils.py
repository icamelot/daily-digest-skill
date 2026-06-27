"""Shared utilities for personal-assistant skill."""
import json
import os
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "config.json"
ENV_FILE = Path.home() / ".ductor" / ".env"


def load_env_secrets() -> dict:
    """Load secrets from ~/.ductor/.env into a dict."""
    secrets = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                secrets[key.strip()] = val.strip().strip('"').strip("'")
    return secrets


def get_env_secret(key: str) -> str:
    """Resolve a single env secret. Raises KeyError if not found."""
    val = os.environ.get(key)
    if val is not None:
        return val
    secrets = load_env_secrets()
    if key in secrets:
        return secrets[key]
    raise KeyError(f"Secret '{key}' not found in environment or ~/.ductor/.env")


def load_config() -> dict:
    """Load and return the full config, resolving _env values."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Config not found at {CONFIG_PATH}. "
            "Copy config.example.json to config.json and fill in your values."
        )
    raw = json.loads(CONFIG_PATH.read_text())
    return resolve_config(raw)


def resolve_config(config: dict) -> dict:
    """Recursively resolve keys ending with '_env' to their secret values."""
    if isinstance(config, dict):
        resolved = {}
        for key, value in config.items():
            if key.endswith("_env"):
                resolved[key.replace("_env", "")] = get_env_secret(value)
            else:
                resolved[key] = resolve_config(value)
        return resolved
    if isinstance(config, list):
        return [resolve_config(item) for item in config]
    return config
