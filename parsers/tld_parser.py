"""
TLD Report Parser (FC WMS — Shopeexpress primary)
Format: .xls (Excel 97-2003)
Structure:
  Row 0  : "Report Truck Load No: TLD2603001646"   ← TLD number here
  Row 1  : blank
  Row 2  : Column headers  (header=2 in pandas)
  Row 3+ : Data rows

Key columns:
  Tracking Number   — SPX tracking (TH...) — always filled
  Order No          — CMGSHP{n}-{shopee_SN}-{seq}
  Brand No          — brand
  Pallet No         — pallet
  Carton No         — carton
  Handover date&time
"""
import io, re
import pandas as pd

REQUIRED_COLUMNS = ["Tracking Number", "Order No"]
KEY_COLUMNS = [
    "No", "Brand No", "Pallet No", "Tracking Number",
    "Carton No", "Order No", "Handover date&time",
]


def parse_tld_xls(file_bytes: bytes) -> tuple[pd.DataFrame, str]:
    """
    Parse TLD Report .xls file.
    Returns (DataFrame, tld_number_string)
    """
    # ── Extract TLD number from title row ─────────────────────────────────────
    raw = pd.read_excel(io.BytesIO(file_bytes), header=None, nrows=1, dtype=str, engine="xlrd")
    title = str(raw.iloc[0, 0]) if not raw.empty else ""
    m = re.search(r"TLD\w+", title, re.IGNORECASE)
    tld_no = m.group(0) if m else ""

    # ── Read data (header at physical row 3 = pandas header=2) ───────────────
    df = pd.read_excel(io.BytesIO(file_bytes), header=2, dtype=str, engine="xlrd")
    df.columns = [str(c).strip() for c in df.columns]

    # Keep relevant columns that exist
    existing_cols = [c for c in KEY_COLUMNS if c in df.columns]
    df = df[existing_cols].copy()

    # Keep rows with a valid Order No
    if "Order No" in df.columns:
        df["Order No"] = df["Order No"].astype(str).str.strip()
        df = df[df["Order No"].notna() & (df["Order No"] != "") & (df["Order No"] != "nan")]

    # Clean Tracking Number
    if "Tracking Number" in df.columns:
        df["Tracking Number"] = df["Tracking Number"].astype(str).str.strip().replace("nan", "")

    return df.reset_index(drop=True), tld_no


def validate_tld_df(df: pd.DataFrame) -> tuple[bool, str]:
    if df.empty:
        return False, "ไม่พบข้อมูลใน TLD Report"
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        return False, f"ไม่พบ Column ที่จำเป็น: {missing}"
    return True, ""