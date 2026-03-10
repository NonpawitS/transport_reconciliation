"""
SPX PDF Parser
Extracts: SPX Tracking Number, Order SN, Pickup Time
Format: PDF with text content (each page = one large text block)
  # SPX Tracking Number Order SN Pickup Time
  1 TH262713777490W 260309ND82J8D4 2026-03-10 09:51:24
1 row = 1 consignment = 1 box
"""
import io
import re
import pdfplumber
import pandas as pd

# Pattern: <number> <TH_tracking> <order_sn> <date time>
# Tracking: starts with TH, alphanumeric
# Order SN: alphanumeric (Shopee format e.g. 260309ND82J8D4)
_ROW_PATTERN = re.compile(
    r"^\s*\d+\s+(TH\w+)\s+(\w+)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})",
    re.MULTILINE,
)


def parse_spx_pdf(file_bytes: bytes) -> pd.DataFrame:
    """
    Parse SPX carrier PDF file.
    Returns DataFrame with columns: tracking, order_sn, pickup_time
    """
    rows = []

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            for match in _ROW_PATTERN.finditer(text):
                tracking = match.group(1).strip()
                order_sn = match.group(2).strip()
                pickup_time = match.group(3).strip()
                rows.append({
                    "tracking": tracking,
                    "order_sn": order_sn,
                    "pickup_time": pickup_time,
                    "page": page_num,
                })

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["tracking", "order_sn", "pickup_time", "page"])

    df = df.drop_duplicates(subset=["order_sn"])
    return df.reset_index(drop=True)


def validate_spx_df(df: pd.DataFrame) -> tuple[bool, str]:
    """Validate parsed SPX DataFrame. Returns (is_valid, error_message)"""
    if df.empty:
        return False, "ไม่พบข้อมูลใน PDF — ตรวจสอบ Format ของไฟล์"
    if "order_sn" not in df.columns:
        return False, "ไม่พบ Column 'Order SN' ใน PDF"
    empty_orders = df["order_sn"].isna().sum() + (df["order_sn"] == "").sum()
    if empty_orders == len(df):
        return False, "Order SN ว่างทุก Row — ตรวจสอบ PDF Format"
    return True, ""