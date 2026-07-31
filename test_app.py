"""
Tests ระดับหน้าแอป — รัน app.py จริงแบบ headless ด้วย Streamlit AppTest

ครอบส่วนแสดงผลที่ unit test ของ engine ไม่ถึง (การ render ตาราง, tab, Excel export)
โดยยัด recon_results เข้า session_state แล้วให้แอป render จากตรงนั้น

รัน:  py -m pytest test_app.py -v
"""
import glob
import os

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from parsers.spx_xlsx_parser import parse_spx_xlsx
from parsers.tld_parser import parse_tld_xls
from parsers.fc_parser import parse_fc_xlsx, build_fc_index, CORE_LABELS
from reconciler.engine import (
    reconcile_spx_tld, enrich_with_fc, match_key,
    build_order_groups, annotate_order_groups,
)

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app.py")
EXAMPLES = os.path.join(HERE, "Example File")

needs_examples = pytest.mark.skipif(
    not os.path.isdir(EXAMPLES), reason="ไม่มีโฟลเดอร์ 'Example File/'"
)


def _run_with(results: dict, timeout: int = 60) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=timeout)
    at.session_state["recon_results"] = results
    at.run()
    assert not at.exception, f"แอป raise exception: {at.exception}"
    return at


def _blank_results(result) -> dict:
    return {
        "result": result, "spx_count": 0, "spx_files": ["x.xlsx"],
        "n_dup": 0, "fc_stats": None, "order_stats": None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# หน้าเริ่มต้น
# ═══════════════════════════════════════════════════════════════════════════════

def test_app_loads_without_files():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, f"แอป raise exception: {at.exception}"
    assert any("Dispatch Reconciliation" in str(t.value) for t in at.title)


# ═══════════════════════════════════════════════════════════════════════════════
# หน้าแสดงผล — ใช้ไฟล์จริง
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def pipeline():
    """รัน pipeline เหมือนที่ app.py ทำ แล้วคืนชิ้นส่วนที่ใช้ประกอบ recon_results"""
    spx_files = sorted(glob.glob(os.path.join(EXAMPLES, "transport_list_*.xlsx")))
    tld_files = [f for f in sorted(glob.glob(os.path.join(EXAMPLES, "TLD*.xls"))) if "_test" not in f]
    fc_files  = sorted(glob.glob(os.path.join(EXAMPLES, "ExportDO_*.xlsx")))
    if not (spx_files and tld_files and fc_files):
        pytest.skip("ไฟล์ตัวอย่างไม่ครบ")

    spx = pd.concat([parse_spx_xlsx(open(p, "rb").read())[0] for p in spx_files], ignore_index=True)
    for c in ("order_sn", "tracking", "pickup_time", "to_number"):
        spx[c] = spx[c].fillna("").astype(str).str.strip()
    spx["_k"] = match_key(spx, "order_sn", "tracking")
    spx = spx[spx["_k"].ne("")].drop_duplicates(subset=["_k"]).drop(columns=["_k"]).reset_index(drop=True)

    tld, tld_no = parse_tld_xls(open(tld_files[0], "rb").read())
    result = reconcile_spx_tld(spx, tld, tld_no=tld_no)

    fc = parse_fc_xlsx(open(fc_files[0], "rb").read())
    idx = build_fc_index(fc)
    result.matched_df, _ = enrich_with_fc(result.matched_df, fc, idx, CORE_LABELS)

    groups = build_order_groups(fc["_order_no"], fc["_tracking"], fc["Truck Load No (FC)"])
    return result, groups, tld_no, len(spx)


@needs_examples
def test_display_when_no_multi_tracking(pipeline):
    """
    regression: ข้อมูลชุดนี้ไม่มีออเดอร์หลาย tracking เลย
    เดิม pd.concat([]) ทำให้แอปพังด้วย ValueError: No objects to concatenate
    """
    result, groups, tld_no, n_spx = pipeline
    result.matched_df, st_m = annotate_order_groups(result.matched_df, groups, current_load=tld_no)
    result.extra_in_wms_df, st_e = annotate_order_groups(result.extra_in_wms_df, groups, current_load=tld_no)
    assert st_m["multi"] == 0 and st_e["multi"] == 0, "fixture ควรไม่มี multi-tracking"

    at = _run_with({
        "result": result, "spx_count": n_spx, "spx_files": ["a.xlsx"], "n_dup": 0,
        "fc_stats": {"fc_rows": 78254, "n_cols": len(CORE_LABELS),
                     "matched": len(result.matched_df), "missing": 0, "extra": 0},
        "order_stats": {"scope": "FC Export DO (ทั้งคลัง)", "multi": 0, "cross_load": 0},
    })
    # ต้องไม่ระเบิด และไม่ขึ้นคำเตือนที่ไม่เกี่ยว
    assert not any("แยกเป็นหลาย Tracking" in str(w.value) for w in at.warning)


@needs_examples
def test_display_all_matched_shows_clean_summary(pipeline):
    """ไฟล์ตัวอย่าง match 100% — ต้องสรุปว่าตรงกันทั้งหมด ไม่มีแท็บขาด/เกิน"""
    result, _, _, n_spx = pipeline
    at = _run_with(_blank_results(result))
    assert any("ตรงกันทั้งหมด" in str(m.value) for m in at.success)
    # แท็บเดิมที่สื่อผิดต้องหายไปแล้ว
    assert not any("เช็ค box ขาด/เกิน" in str(t.value) for t in at.markdown)


@needs_examples
def test_display_flags_probable_mapping_failure(pipeline):
    """
    ค้างทั้งสองฝั่งฝั่งละ 1 โดย Tracking ต่างกันตัวเดียว
    → ต้องเตือนว่าเป็นปัญหา mapping ไม่ใช่ของหาย 1 + เกิน 1
    """
    result, _, _, n_spx = pipeline
    result.missing_in_wms_df = pd.DataFrame([{
        "Status": "Missing in WMS ⚠️", "SPX TO No.": "TO-A",
        "Order SN (SPX)": "", "SPX Tracking": "TH262713777490W", "Pickup Time": "",
    }])
    result.extra_in_wms_df = pd.DataFrame([{
        "Status": "Extra in WMS ⚠️", "Order No": "260731ZZZZZZ-00",
        "Tracking Number": "TH262713777490X", "Create date&time": "",
    }])
    result.summary = {**result.summary, "missing_in_wms": 1, "extra_in_wms": 1}

    at = _run_with(_blank_results(result))
    assert any("Mapping" in str(e.value) for e in at.error), "ต้องเตือนเรื่อง mapping"
    assert any("จำนวนเท่ากันแต่จับคู่ไม่ได้" in str(w.value) for w in at.warning)


@needs_examples
def test_display_with_multi_tracking(pipeline):
    """เคสที่มีออเดอร์แยกหลาย tracking ข้าม Load — ต้อง render ตารางและเตือน"""
    result, groups, tld_no, n_spx = pipeline

    # หยิบออเดอร์จริงที่กระจายข้าม Load มาใส่แทน matched_df
    cross = [(sn, g) for sn, g in groups.items()
             if len(g["trackings"]) > 1 and len(g["loads"]) > 1]
    assert cross, "ต้องมีเคส cross-load ในไฟล์ตัวอย่าง"
    sn, g = cross[0]

    fake = result.matched_df.head(1).copy()
    fake["Order No"] = sn
    fake["Tracking Number"] = g["trackings"][0]
    fake["SPX Tracking"] = g["trackings"][0]
    fake, stats = annotate_order_groups(fake, groups, current_load=g["loads"][0])
    assert stats == {"multi": 1, "cross_load": 1}

    result.matched_df = fake
    result.extra_in_wms_df, _ = annotate_order_groups(result.extra_in_wms_df, groups, current_load=tld_no)

    at = _run_with({
        "result": result, "spx_count": n_spx, "spx_files": ["a.xlsx"], "n_dup": 0,
        "fc_stats": None,
        "order_stats": {"scope": "FC Export DO (ทั้งคลัง)", "multi": 1, "cross_load": 1},
    })
    assert any("แยกเป็นหลาย Tracking" in str(w.value) for w in at.warning)
    assert any("คนละ Truck Load" in str(e.value) for e in at.error)


@needs_examples
def test_display_without_fc(pipeline):
    """ไม่อัปโหลด FC — ต้อง render ได้ปกติ ไม่มี enrichment/order columns"""
    result, _, _, n_spx = pipeline
    at = _run_with(_blank_results(result))
    assert any("Reconcile สำเร็จ" in str(m.value) for m in at.success)


@needs_examples
def test_display_with_empty_result_tables(pipeline):
    """ตารางว่างทุกอัน (เช่น TLD ไม่ match อะไรเลย) ต้องไม่พัง"""
    result, _, _, n_spx = pipeline
    result.matched_df = pd.DataFrame()
    result.missing_in_wms_df = pd.DataFrame()
    result.extra_in_wms_df = pd.DataFrame()
    at = _run_with(_blank_results(result))
    assert not at.exception


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
