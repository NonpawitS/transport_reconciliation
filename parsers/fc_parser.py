"""
FC WMS Export DO Parser — ใช้เป็น "แหล่งเติมข้อมูล" (enrichment) ไม่ใช่ระบบที่เอามาเทียบ

Format: .xlsx, Row 1 = empty, Row 2 = header (header=1), ~78 columns
Join key: 3PL Transport Tracking No  ↔  TLD Tracking Number  (1:1)
          Order no segment           ↔  TLD Order No segment (fallback)

ข้อมูลที่เติมให้:
  เลขที่ Order, วันที่ Order, Handover date/time, Brand, จำนวนชิ้น (สั่ง/หยิบ/กล่อง),
  สถานะ Cancel + เหตุผล, Carrier, Truck Load No, Channel ฯลฯ
"""
import io
import pandas as pd

TRACKING_COLUMN = "3PL Transport Tracking No"
ORDER_COLUMN    = "Order no"

# ── ข้อมูลหลัก — เติมให้เสมอ ──────────────────────────────────────────────────
CORE_FIELDS = [
    ("เลขที่ Order (FC)", "Order no"),
    ("วันที่ Order (FC)",  ("Create Date", "Create Time")),
    ("Handover (FC)",      ("Hand Over Date", "Hand Over Time")),
    ("Brand (FC)",         "Brand In Article"),
    ("จำนวนสั่ง (ชิ้น)",   "Order qty"),
    ("จำนวนหยิบ (ชิ้น)",   "Pick Qty"),
    ("Cancel?",            "Web Cancel Status"),
    ("จำนวน Cancel",       "Sum Of Cancel Qty"),
]

# ── ข้อมูลเสริม — เติมเมื่อผู้ใช้เลือก "แบบเต็ม" ───────────────────────────────
EXTENDED_FIELDS = [
    ("เหตุผล Cancel",       "Cancel Reason"),
    ("เหตุผล Short",        "Short Reason"),
    ("จำนวนกล่อง",          "Total Box"),
    ("น้ำหนัก (kg)",        "Total Weight"),
    ("Weborder DO",         "Weborder DO"),
    ("Invoice No",          "Invoice No"),
    ("Packing No",          "Packing No"),
    ("SO (SAP)",            "SO (SAP)"),
    ("Carrier (FC)",        "Carrier"),
    ("Truck Load No (FC)",  "Truck Load No"),
    ("Channel",             "Document Type Name"),
    ("Marketplace",         "Group Order Type"),
    ("ลูกค้า",              "Sold To Name"),
    ("ทะเบียนรถ (FC)",      "Vehicle Registration"),
    ("จังหวัด",             "Province"),
    ("Load date&time (FC)", ("Load Date", "Load Time")),
]

ALL_FIELDS      = CORE_FIELDS + EXTENDED_FIELDS
CORE_LABELS     = [label for label, _ in CORE_FIELDS]
EXTENDED_LABELS = [label for label, _ in EXTENDED_FIELDS]


def _clean(s: pd.Series) -> pd.Series:
    """
    → string ที่สะอาด, missing เป็น "" เสมอ
    (pandas 3 เก็บ missing เป็น pd.NA ซึ่ง .astype(str) ไม่แปลงให้ ต้อง fillna ก่อน)
    """
    return (s.fillna("").astype(str).str.strip()
             .replace({"nan": "", "NaN": "", "NaT": "", "None": "", "<NA>": ""}))


def _source_columns() -> set[str]:
    """ชื่อคอลัมน์ต้นทางทั้งหมดที่ต้องอ่านจากไฟล์"""
    cols = {TRACKING_COLUMN, ORDER_COLUMN}
    for _, src in ALL_FIELDS:
        cols.update(src if isinstance(src, tuple) else (src,))
    return cols


def _read_raw(file_bytes: bytes) -> pd.DataFrame:
    """
    อ่านเฉพาะคอลัมน์ที่ใช้ — ไฟล์จริงมี ~78 คอลัมน์ / หลายหมื่นแถว

    ใช้ engine 'calamine' (Rust) ถ้ามี เพราะเร็วกว่า openpyxl ~7 เท่า
    (78k rows: ~6 วิ เทียบกับ ~46 วิ) ถ้าไม่มีก็ fallback เป็น default engine
    """
    need = _source_columns()
    usecols = lambda c: str(c).strip() in need  # noqa: E731
    try:
        return pd.read_excel(io.BytesIO(file_bytes), header=1, dtype=str,
                             engine="calamine", usecols=usecols)
    except ImportError:
        return pd.read_excel(io.BytesIO(file_bytes), header=1, dtype=str,
                             usecols=usecols)


def parse_fc_xlsx(file_bytes: bytes) -> pd.DataFrame:
    """
    อ่าน FC Export DO แล้ว normalize ชื่อคอลัมน์เป็นชื่อที่ใช้แสดงผล
    คอลัมน์ผลลัพธ์: _tracking, _order_no + ชื่อ label ตาม ALL_FIELDS
    """
    raw = _read_raw(file_bytes)
    raw.columns = [str(c).strip() for c in raw.columns]

    out = pd.DataFrame(index=raw.index)
    out["_tracking"] = _clean(raw[TRACKING_COLUMN]) if TRACKING_COLUMN in raw.columns else ""
    out["_order_no"] = _clean(raw[ORDER_COLUMN])    if ORDER_COLUMN    in raw.columns else ""

    for label, src in ALL_FIELDS:
        if isinstance(src, tuple):
            # คู่ Date + Time → รวมเป็นค่าเดียว "DD/MM/YYYY HH:MM:SS"
            d_col, t_col = src
            d = _clean(raw[d_col]) if d_col in raw.columns else pd.Series("", index=raw.index)
            t = _clean(raw[t_col]) if t_col in raw.columns else pd.Series("", index=raw.index)
            out[label] = (d + " " + t).str.strip()
        else:
            out[label] = _clean(raw[src]) if src in raw.columns else ""

    # เก็บเฉพาะแถวที่มี Order no
    return out[out["_order_no"].ne("")].reset_index(drop=True)


def build_fc_index(fc_df: pd.DataFrame) -> dict[str, int]:
    """
    สร้าง lookup: key → ตำแหน่งแถวใน fc_df
    ลำดับความสำคัญ: Tracking Number > Order No เต็ม > segment ของ Order No (≥6 ตัวอักษร)
    key ที่มาจาก tracking จะไม่ถูก key อื่นเขียนทับ
    """
    index: dict[str, int] = {}

    # 1) tracking (ความสำคัญสูงสุด)
    for pos, trk in enumerate(fc_df["_tracking"]):
        if trk and trk not in index:
            index[trk] = pos
    tracking_keys = set(index)

    # 2) Order No เต็ม + segment
    for pos, ono in enumerate(fc_df["_order_no"]):
        if not ono:
            continue
        if ono not in tracking_keys and ono not in index:
            index[ono] = pos
        for part in ono.split("-"):
            p = part.strip()
            if len(p) >= 6 and p not in tracking_keys and p not in index:
                index[p] = pos

    return index


def validate_fc_df(fc_df: pd.DataFrame) -> tuple[bool, str]:
    if fc_df.empty:
        return False, "ไม่พบข้อมูลใน FC Export DO"
    if fc_df["_tracking"].ne("").sum() == 0 and fc_df["_order_no"].ne("").sum() == 0:
        return False, "ไม่พบทั้ง 3PL Transport Tracking No และ Order no — ตรวจสอบว่าเป็นไฟล์ Export DO"
    return True, ""
