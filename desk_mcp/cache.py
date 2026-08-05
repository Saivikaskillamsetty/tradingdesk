"""On-disk response cache shared by every external data source.

EDGAR, FRED and the screener all fetch documents that change slowly and cost
either a rate-limit budget or an API quota to refetch. They cache the same way
for the same reasons, so the mechanics live here rather than three times over.

A cache read that fails is never fatal: a truncated or unparseable file is
treated as a miss and refetched.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path(
    os.environ.get("DESK_CACHE_DIR", Path(__file__).resolve().parents[1] / ".cache")
)


def _path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def read(key: str, ttl: int) -> Any | None:
    """Cached payload if present and fresh, otherwise None.

    A negative ttl means "never expire", which suits documents that are
    immutable once filed.
    """
    path = _path(key)
    if not path.exists():
        return None
    if ttl >= 0 and time.time() - path.stat().st_mtime > ttl:
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def write(key: str, payload: Any) -> None:
    """Store a payload under `key`, atomically."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(key)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)
