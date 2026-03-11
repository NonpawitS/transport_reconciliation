"""
Gemini Flash AI integration for Dispatch Reconciliation
Features: Summary report, Explain missing, Anomaly detect, Fuzzy match hint
"""
import pandas as pd
import streamlit as st

try:
    import google.generativeai as genai
    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False


def _get_model():
    if not _GENAI_AVAILABLE:
        return None
    key = st.secrets.get("GEMINI_API_KEY", "")
    if not key:
        return None
    genai.configure(api_key=key)
    return genai.GenerativeModel("gemini-1.5-flash")


def is_available() -> bool:
    return _GENAI_AVAILABLE and bool(st.secrets.get("GEMINI_API_KEY", ""))


def generate_summary_report(summary: dict, carrier_type: str, wms_type: str, filter_value: str) -> str:
    """สรุปผลภาษาไทยพร้อม action items"""
    model = _get_model()
    if not model:
        return "❌ Gemini ไม่พร้อมใช้งาน"

    filter_info = f"Filter: {filter_value}" if filter_value else "ไม่ได้ filter (ทั้งไฟล์)"
    prompt = f"""คุณเป็นผู้เชี่ยวชาญด้าน Logistics และ Warehouse Management
สรุปผล Dispatch Reconciliation นี้เป็นภาษาไทย กระชับ ชัดเจน พร้อม Action Items

ข้อมูล:
- Carrier: {carrier_type}, WMS: {wms_type}
- {filter_info}
- Carrier total: {summary.get('carrier_total', 0)} orders
- WMS total: {summary.get('wms_total', 0)} orders
- Matched: {summary.get('matched', 0)} orders
- Missing in WMS: {summary.get('missing_in_wms', 0)} orders (3PL รับแต่คลังไม่มีบันทึก)
- Extra in WMS: {summary.get('extra_in_wms', 0)} orders (คลังปล่อยแต่ 3PL ไม่รับ)
- Match Rate: {summary.get('match_rate', 0)}%

กรุณาสรุปใน format นี้:
1. ภาพรวมสถานะ (1-2 ประโยค)
2. ประเด็นที่ต้องติดตาม (bullet points)
3. Action Items เร่งด่วน (ถ้ามี)"""

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"❌ Gemini Error: {e}"


def explain_missing(missing_df: pd.DataFrame, carrier_type: str, wms_type: str) -> str:
    """อธิบายสาเหตุที่ orders หายจาก WMS"""
    model = _get_model()
    if not model:
        return "❌ Gemini ไม่พร้อมใช้งาน"

    n = len(missing_df)
    if n == 0:
        return "✅ ไม่มี Missing orders"

    # Sample up to 10 rows for context
    sample = missing_df.head(10).to_string(index=False)

    prompt = f"""คุณเป็นผู้เชี่ยวชาญด้าน Logistics
มี {n} orders ที่ {carrier_type} รับไปแล้ว แต่ไม่พบใน WMS ({wms_type})

ตัวอย่าง orders ที่หาย:
{sample}

วิเคราะห์และอธิบายภาษาไทยว่า:
1. สาเหตุที่เป็นไปได้ว่าทำไม orders เหล่านี้ถึงไม่อยู่ใน WMS
2. ความเสี่ยงที่เกิดขึ้น
3. วิธีตรวจสอบเพิ่มเติม

กระชับ ไม่เกิน 5 bullet points"""

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"❌ Gemini Error: {e}"


def detect_anomalies(missing_df: pd.DataFrame, extra_df: pd.DataFrame, matched_df: pd.DataFrame) -> str:
    """หา pattern ผิดปกติใน reconciliation result"""
    model = _get_model()
    if not model:
        return "❌ Gemini ไม่พร้อมใช้งาน"

    stats = {
        "missing": len(missing_df),
        "extra": len(extra_df),
        "matched": len(matched_df),
    }

    # Check pickup time patterns if available
    time_info = ""
    if "Pickup Time" in missing_df.columns and not missing_df.empty:
        times = missing_df["Pickup Time"].dropna().astype(str)
        if not times.empty:
            time_info = f"\nPickup times ของ missing orders: {times.head(5).tolist()}"

    prompt = f"""วิเคราะห์ผล Reconciliation หา pattern ผิดปกติ:

สถิติ:
- Matched: {stats['matched']}
- Missing in WMS: {stats['missing']}
- Extra in WMS: {stats['extra']}
{time_info}

ตอบภาษาไทย:
1. Pattern ผิดปกติที่พบ (ถ้ามี)
2. ความน่าจะเป็นของแต่ละสาเหตุ
3. ข้อสังเกตสำคัญ

ถ้าข้อมูลปกติให้บอกว่าปกติ ไม่ต้องแต่งเรื่อง"""

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"❌ Gemini Error: {e}"


def fuzzy_match_hint(missing_df: pd.DataFrame, extra_df: pd.DataFrame) -> str:
    """แนะนำ orders ที่อาจจับคู่กันได้แม้ format ต่างกัน"""
    model = _get_model()
    if not model:
        return "❌ Gemini ไม่พร้อมใช้งาน"

    if missing_df.empty or extra_df.empty:
        return "ℹ️ ต้องมีทั้ง Missing และ Extra orders จึงจะ Fuzzy Match ได้"

    miss_col = missing_df.columns[1] if len(missing_df.columns) > 1 else missing_df.columns[0]
    extra_col = extra_df.columns[1] if len(extra_df.columns) > 1 else extra_df.columns[0]

    miss_sample = missing_df[miss_col].dropna().head(8).tolist()
    extra_sample = extra_df[extra_col].dropna().head(8).tolist()

    prompt = f"""ดู order numbers สองชุดนี้และหาคู่ที่น่าจะเป็น order เดียวกัน:

Missing (Carrier มี แต่ WMS ไม่มี):
{miss_sample}

Extra (WMS มี แต่ Carrier ไม่มี):
{extra_sample}

ถ้าเห็นคู่ที่น่าจะ match กัน (เช่น format ต่างกันเล็กน้อย, เลขคล้ายกัน) ให้แสดงเป็น:
- [Carrier order] ↔ [WMS order] (เหตุผล)

ถ้าไม่มีคู่ที่ชัดเจนให้บอกว่าไม่พบ"""

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"❌ Gemini Error: {e}"