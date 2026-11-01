#!/usr/bin/env python3
"""
Unreal Object Info - Query object details directly from Unreal Engine
Uses RC_GetActorInfo to fetch position, rotation, scale, color, and asset info.
"""

import json
from typing import Any, Dict, Optional

from Scripts.api.rc_client import rc_call


def get_object_info(object_id: str) -> Optional[Dict[str, Any]]:
    """
    Get full object details from Unreal Engine by ID.
    
    Args:
        object_id: The actor ID to query
        
    Returns:
        Dict with keys: id, asset, position, rotation, scale, color
        None if object doesn't exist
        
    Example:
        >>> info = get_object_info("item_living_chair_1")
        >>> print(info["position"])
        [100.0, 200.0, 0.0]
    """
    try:
        result = rc_call("RC_GetActorInfo", {"Id": object_id})
        if result and isinstance(result, str):
            # Parse JSON string returned from Unreal
            data = json.loads(result)
            if data and data.get("id"):
                return data
        elif result and isinstance(result, dict):
            # Already parsed
            if result.get("id"):
                return result
    except Exception as e:
        print(f"[unreal_object_info] Error getting info for {object_id}: {e}")
    
    return None


def object_exists(object_id: str) -> bool:
    """Check if an object exists in Unreal."""
    info = get_object_info(object_id)
    return info is not None

