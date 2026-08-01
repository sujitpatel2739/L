"""
Common utility functions.

Author:
Sujit Patel
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any


SETTINGS_FILE = Path("settings.json")


# ---------------------------------------------------------
# Files
# ---------------------------------------------------------

def ensure_directory(path: str | Path) -> None:
    """
    Create directory if missing.
    """
    Path(path).mkdir(parents=True, exist_ok=True)


def load_json(path: str | Path) -> dict:
    """
    Load JSON file.
    """

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str | Path, data: dict) -> None:
    """
    Save JSON.
    """

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


# ---------------------------------------------------------
# Settings
# ---------------------------------------------------------

def load_settings() -> dict:
    """
    Load application settings.
    """

    if not SETTINGS_FILE.exists():
        raise FileNotFoundError(
            "settings.json not found."
        )

    return load_json(SETTINGS_FILE)


# ---------------------------------------------------------
# Logging
# ---------------------------------------------------------

def setup_logger(settings: dict) -> logging.Logger:

    log_cfg = settings.logging

    ensure_directory(log_cfg.directory)

    log_path = os.path.join(
        log_cfg.directory,
        "app.log"
    )

    logger = logging.getLogger("ScreenAssistant")

    logger.setLevel(log_cfg.level)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(

        "[%(asctime)s] "
        "[%(levelname)s] "
        "%(message)s"
    )

    file_handler = logging.FileHandler(
        log_path,
        encoding="utf-8"
    )

    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()

    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def clamp(value: float,
          minimum: float,
          maximum: float) -> float:

    return max(minimum, min(value, maximum))


def safe_get(dictionary: dict,
             *keys: str,
             default: Any = None):

    current = dictionary

    for key in keys:

        if isinstance(current, dict):

            current = current.get(key)

        else:

            return default

        if current is None:

            return default

    return current


def cm_to_inches(cm: float) -> float:   
    return cm / 2.54


def cm_to_pixels(cm: float,
                 dpi: float) -> int:

    return int(cm_to_inches(cm) * dpi)


# ---------------------------------------------------------
# Pretty Printing
# ---------------------------------------------------------

def banner() -> None:

    print(
        "\n"
        "===============================\n"
        "     Screen Assistant\n"
        "===============================\n"
    )