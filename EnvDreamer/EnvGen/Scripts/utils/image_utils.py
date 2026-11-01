#!/usr/bin/env python3
"""
Image and screenshot handling utilities
"""

import os
import glob
import time
import base64
import shutil
from typing import Optional
from Scripts.core.config import SCREENSHOT_DIR, AI_SHOT_DIR, PUBLIC_IMG_DIR, MAX_IMAGE_BYTES, MAX_SNAPSHOT_COPY_WAIT, SNAPSHOT_RETRY_EVERY


def ensure_public_dir():
    """Ensure the public image directory exists"""
    os.makedirs(PUBLIC_IMG_DIR, exist_ok=True)


def find_latest_screenshot() -> Optional[str]:
    """Find the latest screenshot file"""
    candidates = []
    candidates += glob.glob(os.path.join(SCREENSHOT_DIR, "*.png"))
    candidates += glob.glob(os.path.join(AI_SHOT_DIR, "*.png"))
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def copy_latest_screenshot_to_public(
    wait_for_new: bool = False,
    previous_mtime: Optional[float] = None,
) -> Optional[str]:
    """
    Copy newest shot to /shots/latest.png, optionally waiting a bit
    for UE to finish writing after a snapshot call.
    """
    ensure_public_dir()
    deadline = time.time() + (MAX_SNAPSHOT_COPY_WAIT if wait_for_new else 0)
    latest = find_latest_screenshot()
    while wait_for_new and time.time() < deadline:
        if latest is not None:
            try:
                mtime = os.path.getmtime(latest)
                # When a snapshot request overwrites the existing latest.png,
                # its path does not change. Use mtime to avoid immediately
                # copying the previous screenshot back to the browser.
                if previous_mtime is None or mtime > previous_mtime:
                    break
            except OSError:
                pass
        time.sleep(SNAPSHOT_RETRY_EVERY)
        latest = find_latest_screenshot()

    if latest is None:
        # ensure a placeholder exists
        try:
            from PIL import Image
            blank = os.path.join(PUBLIC_IMG_DIR, "latest.png")
            if not os.path.exists(blank):
                Image.new("RGB", (2,2), (18,18,18)).save(blank)
        except Exception:
            pass
        return None

    target = os.path.join(PUBLIC_IMG_DIR, "latest.png")
    try:
        shutil.copy2(latest, target)
    except Exception:
        # small delay then retry once
        time.sleep(0.2)
        try:
            shutil.copy2(latest, target)
        except Exception:
            pass
    return latest


def clear_windowseditor_screenshots():
    """Clear all screenshots from the WindowsEditor directory"""
    if os.path.isdir(SCREENSHOT_DIR):
        for f in glob.glob(os.path.join(SCREENSHOT_DIR, "*.png")):
            try:
                os.remove(f)
            except Exception:
                pass


def latest_png_b64() -> Optional[str]:
    """
    Returns a data URL of the newest PNG (base64). Respects a max byte cap to avoid
    oversize payloads. Returns None if no image found or too big.
    """
    p = find_latest_screenshot()
    if not p or not os.path.exists(p):
        return None
    try:
        size = os.path.getsize(p)
        if size > MAX_IMAGE_BYTES:
            # optionally you can downscale before encoding; for now just skip
            return None
        with open(p, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return "data:image/png;base64," + b64
    except Exception:
        return None


def get_latest_image_path() -> str:
    """Get the path to the latest image for serving"""
    return os.path.join(PUBLIC_IMG_DIR, "latest.png")
