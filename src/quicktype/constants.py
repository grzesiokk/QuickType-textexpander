from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "QuickType"
APP_VERSION = "1.2.0"
DATA_DIR_NAME = "QuickTypeData"
DATABASE_NAME = "quicktype.sqlite3"
AUTOSTART_VALUE_NAME = "QuickType"
SINGLE_INSTANCE_NAME = "QuickType.Windows.SingleInstance.v1"
INJECTED_EVENT_MARKER = 0x5154595045


def executable_dir() -> Path:
    override = os.environ.get("QUICKTYPE_APP_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    override = os.environ.get("QUICKTYPE_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return executable_dir() / DATA_DIR_NAME


def database_path() -> Path:
    return data_dir() / DATABASE_NAME


def resource_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "resources" / name
