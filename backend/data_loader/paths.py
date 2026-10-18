"""Central location of all data files.

The data directory defaults to `<project>/data` and can be overridden with the
STRAUSS_DATA_DIR environment variable (e.g. to point at the real pilot data).
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("STRAUSS_DATA_DIR", PROJECT_ROOT / "data"))

RULES_JSON = DATA_DIR / "rules.json"
CHALLENGE_XLSX = DATA_DIR / "challenge_data.xlsx"
TAX_CSV = DATA_DIR / "tax_authority_2026.csv"
CAR_ALIASES_JSON = DATA_DIR / "car_aliases.json"
APP_DB = DATA_DIR / "app_data.db"
UNIFIED_CONFIG_JSON = DATA_DIR / "config.json"
