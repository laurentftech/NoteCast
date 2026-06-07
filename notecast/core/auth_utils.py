"""Auth utility functions shared across HTTP handlers and services."""
import json
import math
import time

from notecast.core.models import User


def auth_expires_in_days(user: User) -> int | None:
    """Return days until the auth token expires, or None if unknown/missing."""
    if not user.auth_file.exists():
        return None
    try:
        data = json.loads(user.auth_file.read_bytes())
        expiries = [c["expires"] for c in data.get("cookies", []) if c.get("expires", -1) > 0]
        if not expiries:
            return None
        return math.floor((min(expiries) - time.time()) / 86400)
    except Exception:
        return None
