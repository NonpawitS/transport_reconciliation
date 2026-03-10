"""
WDCS WMS Export Parser
Format: Tab-delimited .txt, 61 columns, Row 1 = Header
Encoding: may be Windows-874 (Thai) or UTF-8
Filter column: Transport_No
Key columns: Web Order, Brand In Article, TotalBox, SumOfPickQty
Note: Vehicleregistration is NOT used as filter — unreliable (manually entered)
"""
import io
import chardet
import pandas as pd

FILTER_COLUMN = "Transport_No"
REQUIRED_COLUMNS = ["Web Order"]
ENCODINGS_TO_TRY = ["cp874", "utf-8", "tis-620", "latin-1"]


def parse_wdcs_txt(file_bytes: bytes, transport_no: str = "") -> pd.DataFrame:
    """
    Parse WDCS tab-delimited .txt file.
    If transport_no is given, filter to only that transport.
    Returns DataFrame.
    """
    detected = chardet.detect(file_bytes[:10000])
    detected_enc = detected.get("encoding") or "utf-8"
    encodings = [detected_enc] + [e for e in ENCODINGS_TO_TRY if e != detected_enc]

    df = None
    last_error = None
    for enc in encodings:
        try:
            df = pd.read_csv(
                io.BytesIO(file_bytes),
                sep="\t",
                encoding=enc,
                dtype=str,
                on_bad_lines="skip",
            )
            break
        except Exception as e:
            last_error = e
            continue

    if df is None:
        raise ValueError(f"ไม่สามารถอ่าน WDCS file ได้: {last_error}")

    df.columns = [str(c).strip() for c in df.columns]

    # Clean Web Order values
    if "Web Order" in df.columns:
        df["Web Order"] = df["Web Order"].astype(str).str.strip()
        df = df[df["Web Order"].notna() & (df["Web Order"] != "") & (df["Web Order"] != "nan")]

    # Filter by Transport_No if provided
    if transport_no and transport_no.strip():
        if FILTER_COLUMN in df.columns:
            df = df[df[FILTER_COLUMN].astype(str).str.strip() == transport_no.strip()]

    return df.reset_index(drop=True)


def get_wdcs_transport_numbers(file_bytes: bytes) -> list[str]:
    """Get unique Transport_No values from WDCS file (for dropdown/hint)."""
    detected = chardet.detect(file_bytes[:10000])
    enc = detected.get("encoding") or "cp874"
    try:
        df = pd.read_csv(io.BytesIO(file_bytes), sep="\t", encoding=enc, dtype=str,
                         usecols=[FILTER_COLUMN], on_bad_lines="skip")
    except Exception:
        try:
            df = pd.read_csv(io.BytesIO(file_bytes), sep="\t", encoding="cp874", dtype=str,
                             usecols=[FILTER_COLUMN], on_bad_lines="skip")
        except Exception:
            return []
    vals = df[FILTER_COLUMN].dropna().str.strip().unique().tolist()
    return sorted([v for v in vals if v and v != "nan"])


def validate_wdcs_df(df: pd.DataFrame, transport_no: str = "") -> tuple[bool, str]:
    """Validate parsed WDCS DataFrame. Returns (is_valid, error_message)"""
    if df.empty:
        if transport_no:
            return False, f"ไม่พบข้อมูลสำหรับ Transport No: '{transport_no}'"
        return False, "ไม่พบข้อมูลใน WDCS file"
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        return False, f"ไม่พบ Column ที่จำเป็น: {missing} — ตรวจสอบว่าเป็น WDCS Export file"
    return True, ""