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

# Normal form: <row number> <tracking> <order_sn> <date time>
_SPACED_ROW_PATTERN = re.compile(
    r"^\s*(\d+)\s+(\S+)\s+(\w+)\s+"
    r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*$"
)

# pdfplumber may concatenate the row number and tracking when the row number
# gets wider, for example ``1000TH265...`` or ``100012345...`` when tracking
# itself starts with a digit. In that form the boundary is ambiguous, so split
# it using the expected sequential row number.
_JOINED_ROW_PATTERN = re.compile(
    r"^\s*(\S+)\s+(\w+)\s+"
    r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*$"
)


def _parse_page_text(
    text: str,
    page_num: int,
    expected_row: int | None = None,
) -> list[dict]:
    """Extract SPX rows, using the row sequence to split joined values."""
    rows = []

    for line in text.splitlines():
        spaced = _SPACED_ROW_PATTERN.match(line)
        if spaced:
            row_number = int(spaced.group(1))
            tracking = spaced.group(2)
            order_sn = spaced.group(3)
            pickup_time = spaced.group(4)
        else:
            joined = _JOINED_ROW_PATTERN.match(line)
            if not joined or expected_row is None:
                continue

            row_prefix = str(expected_row)
            first_token = joined.group(1)
            if not first_token.startswith(row_prefix) or first_token == row_prefix:
                continue

            row_number = expected_row
            tracking = first_token[len(row_prefix):]
            order_sn = joined.group(2)
            pickup_time = joined.group(3)

        rows.append({
            "tracking": tracking.strip(),
            "order_sn": order_sn.strip(),
            "pickup_time": pickup_time.strip(),
            "page": page_num,
        })
        expected_row = row_number + 1

    return rows


def parse_spx_pdf(file_bytes: bytes) -> pd.DataFrame:
    """
    Parse SPX carrier PDF file.
    Returns DataFrame with columns: tracking, order_sn, pickup_time
    """
    rows = []

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            rows.extend(_parse_page_text(text, page_num, expected_row=len(rows) + 1))

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
