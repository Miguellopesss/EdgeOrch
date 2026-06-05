"""Runtime path helpers shared by the web and desktop launchers."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def get_bundle_dir() -> Path:
    """Return the directory that contains bundled application assets."""

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return Path(__file__).resolve().parent


def get_runtime_dir() -> Path:
    """Return the writable runtime directory used for .env and state files."""

    configured_runtime_dir = os.getenv("EDGEORCH_RUNTIME_DIR", "").strip()
    if configured_runtime_dir:
        return Path(configured_runtime_dir).expanduser().resolve()

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent


def get_runtime_env_path() -> Path:
    return get_runtime_dir() / ".env"
