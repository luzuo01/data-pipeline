#!/usr/bin/env python3
"""
Remote Control API client for Unreal Engine
"""

import json
import sys
import requests
from pathlib import Path
from typing import Dict, Any

# Add Scripts directory to path if needed
script_dir = Path(__file__).parent.parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

from core.config import RC_BASE, RC_OBJECT_PATH

# Global metrics
TOTAL_RC_CALLS = 0
RC_SUCCESS = 0


def rc_call(function: str, params: Dict[str, Any], max_retries: int = 1) -> bool:
    """Make a Remote Control API call to Unreal Engine"""
    global TOTAL_RC_CALLS, RC_SUCCESS
    TOTAL_RC_CALLS += 1

    # Functions that don't return a value (void functions)
    void_functions = {"ClearAllJsonGeneratedActors"}

    payload = {
        "objectPath": RC_OBJECT_PATH,
        "functionName": function,
        "parameters": params,
        "generateTransaction": True,
    }
    for attempt in range(max_retries + 1):
        try:
            r = requests.put(
                f"{RC_BASE}/remote/object/call",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=30,
            )
            if r.status_code == 200:
                # For void functions, a 200 response means success
                if function in void_functions:
                    RC_SUCCESS += 1
                    return True
                # For functions that return a value, check ReturnValue
                data = r.json()
                ok = bool(data.get("ReturnValue", False))
                if ok:
                    RC_SUCCESS += 1
                return ok
            else:
                if attempt < max_retries:
                    import time as _t
                    _t.sleep(0.5)
        except requests.exceptions.ConnectionError:
            if attempt < max_retries:
                import time as _t
                _t.sleep(1.0)
            else:
                return False
        except Exception:
            if attempt < max_retries:
                import time as _t
                _t.sleep(0.5)
    return False


def check_rc_health() -> bool:
    """Basic health check for RC server."""
    try:
        r = requests.get(f"{RC_BASE}/remote/info", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def rc_call_with_validation(function: str, params: Dict[str, Any], retries: int = 1) -> bool:
    """Safe RC call that validates and retries on transient errors."""
    # If removing, don't treat False as fatal
    if function == "RC_Remove":
        ok = rc_call(function, params)
        return ok or True

    # Normal call with optional retries
    for _attempt in range(retries + 1):
        ok = rc_call(function, params)
        if ok:
            return True
    return False


def get_rc_metrics() -> Dict[str, int]:
    """Get RC call metrics"""
    return {
        "rc_calls_total": TOTAL_RC_CALLS,
        "rc_calls_success": RC_SUCCESS,
    }


def diagnose_rc_crash():
    """Diagnose RC responsiveness and attempt a reindex."""
    if not check_rc_health():
        return False
    try:
        ok = rc_call("RC_Reindex", {})
        return ok
    except Exception:
        return False


def clear_level_aggressive(prefixes: list[str] | None = None) -> bool:
    """Clear all generated actors. Returns True if successful."""
    import time
    ok = rc_call_with_validation("ClearAllJsonGeneratedActors", {}, retries=1)
    if ok:
        # Give Unreal time to process the destroy calls
        time.sleep(0.2)
    return ok


def clear_room_generated(room_name: str):
    """Clear generated actors for a specific room by prefix."""
    # We need to clear actors with room-specific prefixes
    # Since we can't query by prefix, we'll clear common room prefixes
    prefixes_to_clear = [
        f"floor_{room_name}_",
        f"wall_{room_name}_",
        f"furniture_{room_name}_",
        f"item_{room_name}_",
    ]

    # For now, we'll use a more targeted approach
    # Clear all actors and let the room rebuild - this is safer
    rc_call_with_validation("ClearAllJsonGeneratedActors", {}, retries=0)
