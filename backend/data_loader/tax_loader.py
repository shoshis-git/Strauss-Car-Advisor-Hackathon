"""Tax Authority value-of-use (שווי שימוש) database.

Loads the extended 2026 CSV of the Israel Tax Authority (windows-1255), cleans
it (strips column names and every text value) and stores it in an indexed
SQLite table inside `app_data.db`. Lookups use the official 5-field key:
שנת מס + שנת רישום + קוד סוג רכב + קוד תוצר + קוד דגם.

The SQLite table is rebuilt automatically whenever the source CSV changes
(size / mtime fingerprint), so in the pilot one only swaps the CSV file.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Optional

import pandas as pd

from . import paths

CSV_ENCODING = "windows-1255"
TABLE = "value_of_use"

# Hebrew CSV column (after strip) -> SQLite column
COLUMN_MAP: dict[str, str] = {
    "שנת מס": "tax_year",
    "שנת רישום": "registration_year",
    "קוד סוג רכב": "vehicle_type_code",
    "קוד תוצר": "manufacturer_code",
    "תוצר": "manufacturer_name",
    "קוד דגם": "model_code",
    "תאור דגם": "model_description",
    "סוג דלק": "fuel_type_code",
    "כינוי מסחרי": "commercial_name",
    "לא-0.כן-1 אוטומט": "automatic",
    "1-חשמלי.2-היברידי.3-פלג-אין": "drive_code",
    "ציון ירוק": "green_score",
    "דרגת זיהום": "pollution_level",
    "נפח מנוע": "engine_volume",
    "משקל": "weight",
    "תאריך תחולה": "effective_date",
    "מחיר מחירון": "list_price",
    "מחיר מתואם": "adjusted_price",
    "שווי שימוש": "value_of_use",
}
INT_COLUMNS = [
    "tax_year", "registration_year", "vehicle_type_code", "manufacturer_code",
    "model_code", "fuel_type_code", "automatic", "drive_code", "green_score",
    "pollution_level", "engine_volume", "weight", "list_price",
    "adjusted_price", "value_of_use",
]
KEY_COLUMNS = ["tax_year", "registration_year", "vehicle_type_code",
               "manufacturer_code", "model_code"]


@dataclass(frozen=True)
class VehicleValueRecord:
    tax_year: int
    registration_year: int
    vehicle_type_code: int
    manufacturer_code: int
    manufacturer_name: str
    model_code: int
    model_description: str
    fuel_type_code: int
    commercial_name: str
    automatic: bool
    drive_code: int
    green_score: int
    pollution_level: int
    engine_volume: int
    weight: int
    effective_date: str
    list_price: int
    adjusted_price: int
    value_of_use: int
    drive_name: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class DuplicateKeyError(LookupError):
    """More than one record matched the full official key (should never happen)."""


def read_tax_csv(csv_path: Path = paths.TAX_CSV) -> pd.DataFrame:
    """Read and clean the Tax Authority CSV into a typed DataFrame."""
    df = pd.read_csv(csv_path, encoding=CSV_ENCODING, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    missing = set(COLUMN_MAP) - set(df.columns)
    if missing:
        raise ValueError(f"Tax CSV is missing columns: {sorted(missing)}")
    df = df[list(COLUMN_MAP)].rename(columns=COLUMN_MAP)
    df = df.apply(lambda s: s.str.strip())
    for col in INT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="raise").astype("int64")
    dupes = df.duplicated(KEY_COLUMNS)
    if dupes.any():
        raise DuplicateKeyError(f"{int(dupes.sum())} duplicate rows on the official key")
    return df


def _fingerprint(csv_path: Path) -> str:
    st = csv_path.stat()
    return f"{csv_path.name}|{st.st_size}|{int(st.st_mtime)}"


def build_database(csv_path: Path = paths.TAX_CSV, db_path: Path = paths.APP_DB) -> int:
    """(Re)build the value_of_use table + key index. Returns number of rows."""
    df = read_tax_csv(csv_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
            df.to_sql(TABLE, conn, index=False)
            conn.execute(
                f"CREATE UNIQUE INDEX idx_{TABLE}_key ON {TABLE} ({', '.join(KEY_COLUMNS)})"
            )
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.executemany(
                "INSERT OR REPLACE INTO meta VALUES (?, ?)",
                [("tax_csv_fingerprint", _fingerprint(csv_path)),
                 ("tax_csv_rows", str(len(df)))],
            )
    finally:
        conn.close()
    return len(df)


def _is_up_to_date(csv_path: Path, db_path: Path) -> bool:
    if not db_path.exists():
        return False
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='tax_csv_fingerprint'"
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    return row is not None and row[0] == _fingerprint(csv_path)


class TaxAuthorityDB:
    """Read-only access to the indexed value-of-use table."""

    def __init__(
        self,
        csv_path: Path = paths.TAX_CSV,
        db_path: Path = paths.APP_DB,
        drive_names: Optional[Mapping[int, str]] = None,
    ):
        if not _is_up_to_date(csv_path, db_path):
            build_database(csv_path, db_path)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # one connection shared by the server's worker threads: queries must not
        # interleave on it (concurrent requests otherwise got empty results -> 500)
        self._query_lock = threading.Lock()
        if drive_names is None:
            from .config_loader import load_rules
            drive_names = load_rules().value_of_use.drive_names
        self._drive_names = dict(drive_names)

    def count(self) -> int:
        with self._query_lock:
            return self._conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]

    def get_value_of_use(
        self,
        manufacturer_code: int,
        model_code: int,
        registration_year: int,
        tax_year: int = 2026,
        vehicle_type_code: int = 1,
    ) -> Optional[VehicleValueRecord]:
        """Lookup by the full official key. Returns None when no record exists."""
        with self._query_lock:
            rows = self._conn.execute(
                f"SELECT * FROM {TABLE} WHERE tax_year=? AND registration_year=? "
                "AND vehicle_type_code=? AND manufacturer_code=? AND model_code=?",
                (int(tax_year), int(registration_year), int(vehicle_type_code),
                 int(manufacturer_code), int(model_code)),
            ).fetchall()
        if not rows:
            return None
        if len(rows) > 1:
            raise DuplicateKeyError(f"{len(rows)} records for one official key")
        r = dict(rows[0])
        r["automatic"] = bool(r["automatic"])
        r["drive_name"] = self._drive_names.get(r["drive_code"])
        return VehicleValueRecord(**r)

    def close(self) -> None:
        self._conn.close()


_default_db: Optional[TaxAuthorityDB] = None
_lock = threading.Lock()


def get_tax_db() -> TaxAuthorityDB:
    global _default_db
    with _lock:
        if _default_db is None:
            _default_db = TaxAuthorityDB()
        return _default_db


def get_value_of_use(
    manufacturer_code: int,
    model_code: int,
    registration_year: int,
    tax_year: int = 2026,
    vehicle_type_code: int = 1,
) -> Optional[VehicleValueRecord]:
    """Official value-of-use lookup against the default data directory."""
    return get_tax_db().get_value_of_use(
        manufacturer_code, model_code, registration_year, tax_year, vehicle_type_code
    )
