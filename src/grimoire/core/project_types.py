"""Shared project type constants."""

from __future__ import annotations

VALID_PROJECT_TYPES: tuple[str, ...] = (
    "webapp",
    "api",
    "service",
    "infrastructure",
    "library",
    "framework",
    "cli",
    "generic",
    "meta",
)
