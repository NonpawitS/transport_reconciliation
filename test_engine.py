"""
Tests สำหรับ reconciliation pipeline

รัน:  py -m pytest test_engine.py -v
      (หรือ  py test_engine.py  เพื่อรันแบบไม่ต้องมี pytest)

เทสต์ที่ใช้ไฟล์จริงจะ skip อัตโนมัติถ้าไม่มีโฟลเดอร์ 'Example File/'
"""
import glob
import os

import pandas as pd
import pytest

from parsers.spx_xlsx_parser import parse_spx_xlsx, validate_spx_xlsx_df
from parsers.tld_parser import parse_tld_xls, validate_tld_df
from parsers.fc_parser import (
    parse_fc_xlsx, build_fc_index, validate_fc_df, CORE_LABELS, EXTENDED_LABELS,
)
from reconciler.engine import (
    reconcile_spx_tld, enrich_with_fc, match_key, FC_FOUND_COL,
    extract_order_sn, build_order_groups, annotate_order_groups,
    ORDER_SN_COL, ORDER_NTRK_COL, ORDER_OTHER_COL, ORDER_LOADS_COL,
    build_unmatched_df, pair_unmatched, collect_multi_tracking,
    SIDE_COL, SIDE_SPX_ONLY, SIDE_TLD_ONLY, UNMATCHED_COLS,
)


def _spx_side(*rows) -> pd.DataFrame:
    """สร้าง missing_in_wms_df จำลอง — (tracking, order_sn)"""
    return pd.DataFrame([{"SPX Tracking": t, "Order SN (SPX)": sn,
                          "SPX TO No.": "TO-A", "Pickup Time": ""} for t, sn in rows])


def _tld_side(*rows) -> pd.DataFrame:
    """สร้าง extra_in_wms_df จำลอง — (tracking, order_no)"""
    return pd.DataFrame([{"Tracking Number": t, "Order No": o,
                          "Create date&time": ""} for t, o in rows])

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(HERE, "Example File")


def _examples(pattern: str) -> list[str]:
    return sorted(glob.glob(os.path.join(EXAMPLES, pattern)))


needs_examples = pytest.mark.skipif(
    not os.path.isdir(EXAMPLES), reason="ไม่มีโฟลเดอร์ 'Example File/'"
)


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests — ไม่ต้องใช้ไฟล์
# ═══════════════════════════════════════════════════════════════════════════════

def test_match_key_prefers_order_sn():
    df = pd.DataFrame([{"order_sn": "260309ND82J8D4", "tracking": "TH262713777490W"}])
    assert match_key(df, "order_sn", "tracking").tolist() == ["260309ND82J8D4"]


def test_match_key_falls_back_to_tracking():
    """ไฟล์ SPX Excel ไม่มี Order SN — ต้อง fallback เป็น TRK: ไม่ใช่คืน '' """
    df = pd.DataFrame([{"order_sn": "", "tracking": "TH262713777490W"}])
    assert match_key(df, "order_sn", "tracking").tolist() == ["TRK:TH262713777490W"]


def test_match_key_empty_when_both_missing():
    df = pd.DataFrame([{"order_sn": "", "tracking": ""}])
    assert match_key(df, "order_sn", "tracking").tolist() == [""]


def test_match_key_handles_pandas_na():
    """pandas 3 เก็บ missing เป็น pd.NA — str(pd.NA) = '<NA>' ต้องไม่หลุดออกมาเป็นคีย์"""
    df = pd.DataFrame({"order_sn": pd.Series([None], dtype="str"),
                       "tracking":  pd.Series(["TH1"], dtype="str")})
    assert match_key(df, "order_sn", "tracking").tolist() == ["TRK:TH1"]


def test_dedupe_does_not_collapse_rows_without_order_sn():
    """regression: drop_duplicates บน order_sn ล้วนๆ เคยยุบ 1,527 แถวเหลือ 1"""
    df = pd.DataFrame({"order_sn": [""] * 3, "tracking": ["TH1", "TH2", "TH3"]})
    df["_k"] = match_key(df, "order_sn", "tracking")
    assert len(df.drop_duplicates(subset=["_k"])) == 3


def test_reconcile_matches_by_tracking_without_order_sn():
    spx = pd.DataFrame([
        {"tracking": "TH001", "order_sn": "", "pickup_time": "2026-07-31 10:00:00", "to_number": "TO-A"},
        {"tracking": "TH002", "order_sn": "", "pickup_time": "2026-07-31 10:01:00", "to_number": "TO-A"},
    ])
    tld = pd.DataFrame([{"Tracking Number": "TH001", "Order No": "260731ABCDEF-00"}])

    r = reconcile_spx_tld(spx, tld, tld_no="TLD001")
    assert r.summary["matched"] == 1
    assert r.summary["matched_by_tracking"] == 1
    assert r.summary["missing_in_wms"] == 1
    # SPX TO No. ต้องไหลไปถึงผลลัพธ์ทั้งสองฝั่ง
    assert r.matched_df["SPX TO No."].tolist() == ["TO-A"]
    assert r.missing_in_wms_df["SPX TO No."].tolist() == ["TO-A"]


def test_enrich_with_fc_joins_by_tracking_then_order():
    fc = pd.DataFrame({
        "_tracking": ["TH001", ""],
        "_order_no": ["260731AAAAAA-00", "260731BBBBBB-00"],
        "Brand (FC)": ["CE", "CI"],
    })
    idx = build_fc_index(fc)
    df = pd.DataFrame([
        {"Tracking Number": "TH001", "Order No": ""},                 # เจอผ่าน tracking
        {"Tracking Number": "",      "Order No": "260731BBBBBB-00"},  # เจอผ่าน order no
        {"Tracking Number": "TH999", "Order No": "ZZZ"},              # ไม่เจอ
    ])
    out, n = enrich_with_fc(df, fc, idx, ["Brand (FC)"])
    assert n == 2
    assert out["Brand (FC)"].tolist() == ["CE", "CI", ""]
    assert out[FC_FOUND_COL].tolist() == ["✅", "✅", "—"]


def test_enrich_is_noop_without_fc():
    df = pd.DataFrame([{"Tracking Number": "TH001"}])
    out, n = enrich_with_fc(df, pd.DataFrame(), {}, ["Brand (FC)"])
    assert n == 0 and out.equals(df)


# ── Order SN / ออเดอร์หลาย tracking ────────────────────────────────────────────

@pytest.mark.parametrize("order_no,expected", [
    ("2607302FNDAAFV-00",                 "2607302FNDAAFV"),   # Shopee ล้วน
    ("2607303NH00FVN-00",                 "2607303NH00FVN"),
    ("CMGSHP313088592-2607302U57HVHP-01", "2607302U57HVHP"),   # มี prefix CMGSHP
    ("CMGSHP165027260-260725J03AQWXU-02", "260725J03AQWXU"),
    ("2607302FNDAAFV",                    "2607302FNDAAFV"),   # ไม่มี suffix
    ("", ""),
])
def test_extract_order_sn(order_no, expected):
    assert extract_order_sn(order_no) == expected


def test_extract_order_sn_keeps_non_shopee_whole():
    """Mirakl ฯลฯ segment สุดท้ายเป็น 'A' — ห้ามยุบออเดอร์ต่างกันเป็นคีย์เดียว"""
    a = extract_order_sn("CMGMRL-CDS2607312611453174_2076-A-01")
    b = extract_order_sn("CMGMRL-CDS2607312611494499_2114-A-01")
    assert a != b
    assert a == "CMGMRL-CDS2607312611453174_2076-A"


def test_build_order_groups_collects_trackings_and_loads():
    groups = build_order_groups(
        ["CMGSHP1-260725J03AQWXU-01", "CMGSHP1-260725J03AQWXU-02", "260725ZZZZZZZZ-00"],
        ["TH001", "TH002", "TH003"],
        ["TLD_A", "TLD_B", "TLD_A"],
    )
    assert groups["260725J03AQWXU"]["trackings"] == ["TH001", "TH002"]
    assert groups["260725J03AQWXU"]["loads"] == ["TLD_A", "TLD_B"]
    assert groups["260725ZZZZZZZZ"]["trackings"] == ["TH003"]


def test_annotate_flags_split_order_across_loads():
    groups = build_order_groups(
        ["CMGSHP1-260725J03AQWXU-01", "CMGSHP1-260725J03AQWXU-02"],
        ["TH001", "TH002"],
        ["TLD_A", "TLD_B"],
    )
    df = pd.DataFrame([{"Order No": "CMGSHP1-260725J03AQWXU-01", "Tracking Number": "TH001"}])
    out, stats = annotate_order_groups(df, groups, current_load="TLD_A")

    assert out[ORDER_SN_COL].iloc[0] == "260725J03AQWXU"
    assert out[ORDER_NTRK_COL].iloc[0] == 2
    assert out[ORDER_OTHER_COL].iloc[0] == "TH002"     # ไม่รวม tracking ของตัวเอง
    assert out[ORDER_LOADS_COL].iloc[0] == "TLD_B"     # ไม่รวม load ปัจจุบัน
    assert stats == {"multi": 1, "cross_load": 1}


def test_annotate_single_tracking_order_is_clean():
    groups = build_order_groups(["260725ZZZZZZZZ-00"], ["TH003"], ["TLD_A"])
    df = pd.DataFrame([{"Order No": "260725ZZZZZZZZ-00", "Tracking Number": "TH003"}])
    out, stats = annotate_order_groups(df, groups, current_load="TLD_A")
    assert out[ORDER_NTRK_COL].iloc[0] == 1
    assert out[ORDER_OTHER_COL].iloc[0] == ""
    assert out[ORDER_LOADS_COL].iloc[0] == ""
    assert stats == {"multi": 0, "cross_load": 0}


def test_annotate_same_load_split_is_multi_but_not_cross_load():
    groups = build_order_groups(
        ["CMGSHP1-260725J03AQWXU-01", "CMGSHP1-260725J03AQWXU-02"],
        ["TH001", "TH002"], ["TLD_A", "TLD_A"],
    )
    df = pd.DataFrame([{"Order No": "CMGSHP1-260725J03AQWXU-01", "Tracking Number": "TH001"}])
    _, stats = annotate_order_groups(df, groups, current_load="TLD_A")
    assert stats == {"multi": 1, "cross_load": 0}


# ── ตารางไม่ Match + การจับคู่ที่ mapping ไม่ติด ───────────────────────────────

def test_build_unmatched_df_merges_both_sides():
    out = build_unmatched_df(
        _spx_side(("TH001", "260731AAAAAA")),
        _tld_side(("TH002", "260731BBBBBB-00")),
    )
    assert list(out.columns) == UNMATCHED_COLS
    assert out[SIDE_COL].tolist() == [SIDE_SPX_ONLY, SIDE_TLD_ONLY]
    assert out["Tracking"].tolist() == ["TH001", "TH002"]
    # ฝั่ง TLD ต้องแกะ Order SN จาก Order No ให้เทียบกันได้
    assert out["Order SN"].tolist() == ["260731AAAAAA", "260731BBBBBB"]


def test_build_unmatched_df_empty():
    out = build_unmatched_df(pd.DataFrame(), pd.DataFrame())
    assert out.empty and list(out.columns) == UNMATCHED_COLS


def test_pair_matches_on_same_order_sn():
    """Order SN เดียวกันแต่ Tracking คนละตัว — คือของชิ้นเดียวกัน"""
    pairs, stats = pair_unmatched(
        _spx_side(("TH00000001", "260731AAAAAA")),
        _tld_side(("TH99999999", "CMGSHP1-260731AAAAAA-00")),
    )
    assert len(pairs) == 1
    assert pairs["Tracking (SPX)"].iloc[0] == "TH00000001"
    assert pairs["Tracking (TLD)"].iloc[0] == "TH99999999"
    assert "Order SN ตรงกัน" in pairs["สาเหตุที่น่าจะเป็น"].iloc[0]
    assert stats["paired"] == 1


def test_pair_matches_on_tracking_format_difference():
    """ต่างแค่ตัวพิมพ์/ช่องว่าง/ขีด"""
    pairs, _ = pair_unmatched(
        _spx_side((" th-2627 137774 90w ", "")),
        _tld_side(("TH262713777490W", "260731ZZZZZZ-00")),
    )
    assert len(pairs) == 1
    assert "ต่างแค่รูปแบบ" in pairs["สาเหตุที่น่าจะเป็น"].iloc[0]


def test_pair_matches_on_near_tracking():
    """ต่างไม่กี่ตัวอักษร — สแกน/คีย์ผิด"""
    pairs, _ = pair_unmatched(
        _spx_side(("TH262713777490W", "")),
        _tld_side(("TH262713777490X", "260731ZZZZZZ-00")),
    )
    assert len(pairs) == 1
    assert "ต่างกัน 1 ตัวอักษร" in pairs["สาเหตุที่น่าจะเป็น"].iloc[0]


def test_pair_rejects_genuinely_different_trackings():
    """ต่างกันเยอะ = คนละรายการจริง ไม่ควรจับคู่มั่ว"""
    pairs, stats = pair_unmatched(
        _spx_side(("TH111111111111A", "260731AAAAAA")),
        _tld_side(("TH999999999999Z", "260731BBBBBB-00")),
    )
    assert pairs.empty
    assert stats == {"spx_only": 1, "tld_only": 1, "paired": 0, "scan_skipped": False}


def test_pair_needs_both_sides():
    """ค้างฝั่งเดียว = ของหาย/เกินจริง ไม่ใช่ปัญหา mapping"""
    pairs, stats = pair_unmatched(_spx_side(("TH001", "260731AAAAAA")), pd.DataFrame())
    assert pairs.empty and stats["spx_only"] == 1 and stats["tld_only"] == 0


def test_pair_does_not_reuse_the_same_tld_row():
    """SPX 2 แถวต้องไม่จับคู่กับ TLD แถวเดียวกันซ้ำ"""
    pairs, _ = pair_unmatched(
        _spx_side(("TH00000000001", "260731AAAAAA"), ("TH00000000002", "260731AAAAAA")),
        _tld_side(("TH00000000009", "CMGSHP1-260731AAAAAA-00")),
    )
    assert len(pairs) == 1
    assert pairs["Tracking (TLD)"].nunique() == 1


def test_collect_multi_tracking_empty_does_not_raise():
    """regression: pd.concat([]) เคยทำให้แอปพัง"""
    out, n = collect_multi_tracking([pd.DataFrame(), pd.DataFrame()])
    assert out.empty and n == 0
    out, n = collect_multi_tracking([])
    assert out.empty and n == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Integration tests — ใช้ไฟล์จริงใน Example File/
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def spx_df():
    files = _examples("transport_list_*.xlsx")
    if not files:
        pytest.skip("ไม่มีไฟล์ SPX Transport Order ตัวอย่าง")
    frames = []
    for p in files:
        df, _ = parse_spx_xlsx(open(p, "rb").read())
        assert validate_spx_xlsx_df(df)[0]
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out["_k"] = match_key(out, "order_sn", "tracking")
    return (out[out["_k"].ne("")].drop_duplicates(subset=["_k"])
               .drop(columns=["_k"]).reset_index(drop=True))


@pytest.fixture(scope="module")
def tld_data():
    files = _examples("TLD*.xls")
    files = [f for f in files if "_test" not in f]
    if not files:
        pytest.skip("ไม่มีไฟล์ TLD Report ตัวอย่าง")
    return parse_tld_xls(open(files[0], "rb").read())


@pytest.fixture(scope="module")
def fc_data():
    files = _examples("ExportDO_*.xlsx")
    if not files:
        pytest.skip("ไม่มีไฟล์ FC Export DO ตัวอย่าง")
    fc = parse_fc_xlsx(open(files[0], "rb").read())
    return fc, build_fc_index(fc)


@needs_examples
def test_spx_multi_file_concat(spx_df):
    """12 TO files → รวมกันโดยไม่ยุบแถว และ TO Number ติดมาครบ"""
    assert len(spx_df) == spx_df["tracking"].nunique()
    assert spx_df["to_number"].ne("").all()
    assert spx_df["to_number"].nunique() == len(_examples("transport_list_*.xlsx"))


@needs_examples
def test_tld_parser_keeps_all_timestamps(tld_data):
    """Arrival/Handover เคยถูก parser ทิ้ง — ต้องเก็บไว้"""
    tld, tld_no = tld_data
    assert validate_tld_df(tld)[0]
    assert tld_no.startswith("TLD")
    for c in ("Create date&time", "Arrival date&time", "Handover date&time"):
        assert c in tld.columns, c


@needs_examples
def test_reconcile_real_files(spx_df, tld_data):
    tld, tld_no = tld_data
    r = reconcile_spx_tld(spx_df, tld, tld_no=tld_no)
    assert r.summary["match_rate"] == 100.0
    assert r.summary["matched"] == len(spx_df)
    assert r.summary["missing_in_wms"] == 0
    for c in ("SPX TO No.", "Arrival date&time", "Handover date&time"):
        assert c in r.matched_df.columns, c


@needs_examples
def test_fc_enrichment_real_files(spx_df, tld_data, fc_data):
    tld, tld_no = tld_data
    fc, idx = fc_data
    assert validate_fc_df(fc)[0]

    r = reconcile_spx_tld(spx_df, tld, tld_no=tld_no)
    labels = CORE_LABELS + EXTENDED_LABELS
    out, n_found = enrich_with_fc(r.matched_df, fc, idx, labels)

    assert n_found == len(r.matched_df), "FC ต้องเติมข้อมูลได้ครบทุกแถว"
    for label in labels:
        assert label in out.columns, label
    # Truck Load No จาก FC ต้องตรงกับ TLD ที่กำลัง reconcile
    assert (out["Truck Load No (FC)"] == tld_no).all()
    # ต้องไม่มี 'NaN'/'<NA>' หลุดมาเป็นข้อความ
    for label in labels:
        vals = out[label].astype(str).str.strip().str.lower()
        assert not vals.isin(["nan", "<na>", "nat", "none"]).any(), label


@needs_examples
def test_order_groups_from_real_fc(fc_data):
    """FC ทั้งคลังต้องเห็นออเดอร์ Shopee ที่ถูกแยกเป็นหลาย tracking"""
    fc, _ = fc_data
    groups = build_order_groups(fc["_order_no"], fc["_tracking"], fc["Truck Load No (FC)"])
    assert groups

    multi = {sn: g for sn, g in groups.items() if len(g["trackings"]) > 1}
    assert multi, "ควรพบออเดอร์ที่มีหลาย tracking อย่างน้อย 1 รายการ"

    # ที่กระจายข้าม Truck Load — เคสที่ต้องเตือน
    cross = {sn: g for sn, g in multi.items() if len(g["loads"]) > 1}
    assert cross, "ควรพบออเดอร์ที่กล่องกระจายคนละ Load"

    sn, g = next(iter(cross.items()))
    df = pd.DataFrame([{"Order No": sn, "Tracking Number": g["trackings"][0]}])
    out, stats = annotate_order_groups(df, groups, current_load=g["loads"][0])
    assert stats["multi"] == 1 and stats["cross_load"] == 1
    assert g["trackings"][1] in out[ORDER_OTHER_COL].iloc[0]
    assert g["loads"][1] in out[ORDER_LOADS_COL].iloc[0]


@needs_examples
def test_annotate_on_real_reconcile_result(spx_df, tld_data, fc_data):
    """annotate ต้องทำงานบนผลลัพธ์จริงโดยไม่ทำให้จำนวนแถวเปลี่ยน"""
    tld, tld_no = tld_data
    fc, idx = fc_data
    r = reconcile_spx_tld(spx_df, tld, tld_no=tld_no)
    groups = build_order_groups(fc["_order_no"], fc["_tracking"], fc["Truck Load No (FC)"])

    before = len(r.matched_df)
    out, stats = annotate_order_groups(r.matched_df, groups, current_load=tld_no)
    assert len(out) == before
    for c in (ORDER_SN_COL, ORDER_NTRK_COL, ORDER_OTHER_COL, ORDER_LOADS_COL):
        assert c in out.columns, c
    # Order SN ต้องแกะได้ทุกแถว และทุกแถวต้องหากลุ่มเจอ (อย่างน้อย 1 tracking)
    assert out[ORDER_SN_COL].astype(str).str.strip().ne("").all()
    assert (out[ORDER_NTRK_COL] >= 1).all()
    assert stats["multi"] >= 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
