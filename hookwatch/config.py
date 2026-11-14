"""Small, explicit local configuration loader; environment wins over .env."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def settings():
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    return {
        "DATABASE": os.environ.get(
            "DATABASE_PATH", str(ROOT / "instance/hookwatch.sqlite")
        ),
        "ADMIN_TOKEN": os.environ.get("ADMIN_TOKEN", ""),
        "WEBHOOK_SECRET": os.environ.get("WEBHOOK_SECRET", ""),
        "SECRET_KEY": os.environ.get("SECRET_KEY", ""),
        "DEMO_MODE": os.environ.get("DEMO_MODE") == "1",
        "MAX_CONTENT_LENGTH": 64 * 1024,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Strict",
        "SESSION_COOKIE_SECURE": os.environ.get("COOKIE_SECURE") == "1",
        "PERMANENT_SESSION_LIFETIME": 8 * 3600,
    }
