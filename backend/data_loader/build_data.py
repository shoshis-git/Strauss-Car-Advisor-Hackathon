"""Build the derived data files: data/app_data.db and data/config.json.

Usage:  python -m backend.data_loader.build_data
"""
from __future__ import annotations

from . import paths
from .config_loader import export_unified_config, load_config
from .tax_loader import build_database


def main() -> None:
    rows = build_database(paths.TAX_CSV, paths.APP_DB)
    print(f"[tax]    {rows:,} records -> {paths.APP_DB}")
    config = load_config()
    out = export_unified_config(config)
    print(f"[config] {len(config.cars)} cars, {len(config.occupations)} occupations, "
          f"{len(config.employees)} employees, "
          f"{len(config.reference_calculations)} reference calculations -> {out}")


if __name__ == "__main__":
    main()
