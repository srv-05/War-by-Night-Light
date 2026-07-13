"""One blueprint per visualization endpoint group (§5/§6)."""
from __future__ import annotations

from flask import jsonify


def envelope(data, **meta):
    """Every endpoint returns ``{"data": ..., "meta": {...}}`` (§6)."""
    return jsonify({"data": data, "meta": meta})
