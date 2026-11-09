"""Where FP Bridge keeps its files.

  ~/Library/Application Support/FP Bridge/config.yaml   your mapping (created from the default)
  ~/Library/Logs/FP Bridge.log                          log of the menu bar app

FPBRIDGE_HOME overrides the support folder (used by tests).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PACKAGE_DIR / "default_config.yaml"


def support_dir() -> Path:
    d = Path(os.environ.get("FPBRIDGE_HOME") or Path.home() / "Library" / "Application Support" / "FP Bridge")
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_config() -> Path:
    """The config the bridge uses; copied from the default the first time."""
    p = support_dir() / "config.yaml"
    if not p.exists():
        shutil.copyfile(DEFAULT_CONFIG, p)
    return p


def log_file() -> Path:
    if os.environ.get("FPBRIDGE_HOME"):
        return support_dir() / "fpbridge.log"
    d = Path.home() / "Library" / "Logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "FP Bridge.log"


def lock_file() -> Path:
    return support_dir() / ".lock"
