#!/usr/bin/env python3
"""
Configuration constants for Unreal Map Copilot
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Shared local configuration, also consumed by the physical task worker.
_env_path = PROJECT_ROOT.parent / '.env'
if _env_path.is_file():
    for _line in _env_path.read_text(encoding='utf-8').splitlines():
        if _line.strip() and not _line.lstrip().startswith('#') and '=' in _line:
            _key, _value = _line.split('=', 1)
            os.environ.setdefault(_key.strip(), _value.strip().strip('"').strip("'"))

# OpenAI Configuration
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Remote Control Configuration
RC_BASE = "http://127.0.0.1:30010"
RC_OBJECT_PATH = os.getenv(
    "RC_OBJECT_PATH",
    "/Game/EnvGen/Maps/ThirdPersonMap.ThirdPersonMap:PersistentLevel.BP_JsonSceneBuilder_C_UAID_BCFCE7D92346448C02_1693193303"
)

# Screenshot Configuration
SCREENSHOT_DIR = os.getenv(
    "ENVDREAMER_SCREENSHOT_DIR",
    str(PROJECT_ROOT / "Saved" / "Screenshots" / "WindowsEditor"),
)
AI_SHOT_DIR = os.getenv("ENVDREAMER_AI_SHOT_DIR", str(PROJECT_ROOT / "Saved" / "AIShots"))
PUBLIC_IMG_DIR = os.getenv("ENVDREAMER_PUBLIC_IMG_DIR", str(PROJECT_ROOT / "_public_shots"))

# Performance Configuration
CALL_DELAY_SEC = float(os.getenv("RC_CALL_DELAY_SEC", "0.02"))
# UE's high-resolution editor screenshot is written asynchronously. Keep the
# window longer than a single frame so the refresh endpoint does not race the
# file write.
MAX_SNAPSHOT_COPY_WAIT = float(os.getenv("MAX_SNAPSHOT_COPY_WAIT", "90.0"))
SNAPSHOT_RETRY_EVERY = 0.2

# Live Ticker Configuration
LIVE_INTERVAL_SEC = float(os.getenv("LIVE_INTERVAL_SEC", "0.5"))
LIVE_ENABLED = True

# Live Session Configuration
LIVE_MODE_DELAY_SEC = float(os.getenv("LIVE_MODE_DELAY_SEC", "0.01"))  # 10ms for live mode
LIVE_SESSION_AUTO_SNAPSHOT = os.getenv("LIVE_SESSION_AUTO_SNAPSHOT", "1") == "1"

# Plan Limits
MAX_PLAN_CALLS = int(os.getenv("MAX_PLAN_CALLS", "500"))
SNAPSHOT_EVERY_N = int(os.getenv("SNAPSHOT_EVERY_N", "0"))

# Vision Configuration
MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))  # 10MB cap
SNAP_BEFORE_PLAN = os.getenv("SNAP_BEFORE_PLAN", "1") == "1"
VISION_ENABLED = 1

# Action Memory
ACTION_MEMORY_SIZE = 5

# Server Configuration
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 7860
