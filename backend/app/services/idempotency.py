import hashlib
import json
from typing import Any


def request_fingerprint(payload: dict[str, Any]) -> str:
    """Stable hash of the semantic request, used to reject reuse of an
    Idempotency-Key with a different body."""
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
