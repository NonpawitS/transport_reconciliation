"""
FC WMS Export Parser
Format: .xlsx, Row 1 = empty, Row 2 = header (header=1)
Filter column: Truck Load No
Key columns: Weborder DO, 3PL Transport Tracking No, Brand In Article, Total Box, Pick Qty, Carrier
"""
import io
import pandas as pd

FILTER_COLUMN = "Truck Load No"
REQUIRED_COLUMNS = ["Weborder DO"]
KEY_COLUMNS = [
    "Truck Load No",
    "Weborder DO",
    "3PL Transport Tracking No",
    "Brand In Article",
    "Order no",
    "Total Box",
    "Pick Qty",
    "Carrier",
    "Sold To Name",
]


def parse_fc_xlsx(file_bytes: bytes, truck_load_no: str = "") -> pd.DataFrame:
    """
    Parse FC Export DO Excel file.
    If truck_load_no is given, filter to only that load.
    Returns DataFrame.
    """
    df = pd.read_excel(
        io.BytesIO(file_bytes),
        header=1,       # Row 1 empty, Row 2 = header
        dtype=str,
    )

    # Clean column names
    df.columns = [str(c).strip() for c in df.columns]

    # Keep only relevant columns that exist
    existing_cols = [c for c in KEY_COLUMNS if c in df.columns]
    df = df[existing_cols].copy()

    # Clean Weborder DO
    if "Weborder DO" in df.columns:
        df["Weborder DO"] = df["Weborder DO"].astype(str).str.strip()
        df = df[df["Weborder DO"].notna() & (df["Weborder DO"] != "") & (df["Weborder DO"] != "nan")]

    # Filter by Truck Load No if provided
    if truck_load_no and truck_load_no.strip():
        if FILTER_COLUMN in df.columns:
            df = df[df[FILTER_COLUMN].astype(str).str.strip() == truck_load_no.strip()]
    elif FILTER_COLUMN in df.columns:
        # Sort: rows WITH Truck Load No first (latest TLD on top), blank TLD at bottom
        _tld = df[FILTER_COLUMN].astype(str).str.strip().replace("nan", "")
        _has_tld = _tld.ne("")
        df = pd.concat([
            df[_has_tld].sort_values(FILTER_COLUMN, ascending=False, key=lambda s: s.astype(str)),
            df[~_has_tld],
        ], ignore_index=True)

    return df.reset_index(drop=True)


def get_fc_load_numbers(file_bytes: bytes) -> list[str]:
    """Get unique Truck Load No values from FC file (for dropdown/hint)."""
    df = pd.read_excel(io.BytesIO(file_bytes), header=1, dtype=str, usecols=[FILTER_COLUMN])
    df.columns = [str(c).strip() for c in df.columns]
    if FILTER_COLUMN not in df.columns:
        return []
    vals = df[FILTER_COLUMN].dropna().str.strip().unique().tolist()
    return sorted([v for v in vals if v and v != "nan"])


def validate_fc_df(df: pd.DataFrame, truck_load_no: str = "") -> tuple[bool, str]:
    if df.empty:
        if truck_load_no:
            return False, f"ไม่พบข้อมูลสำหรับ Truck Load No: '{truck_load_no}'"
        return False, "ไม่พบข้อมูลใน FC file"
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        return False, f"ไม่พบ Column ที่จำเป็น: {missing}"
    return True, ""