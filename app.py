"""
Dispatch Reconciliation System — v3.0
Reconcile : SPX (PDF + Excel Transport Order, หลายไฟล์ผสมกันได้)  ↔  TLD Report
Enrichment: FC Export DO เติมข้อมูลที่ขาด (เลขที่ Order, Handover, Brand, จำนวนชิ้น, Cancel ฯลฯ)
2 มุมมอง: SPX เป็นหลัก (ขาดอะไรใน WMS) / WMS เป็นหลัก (อะไรใน WMS ที่ไม่มีใน SPX)
"""
import io
import streamlit as st
import pandas as pd

from parsers.spx_parser      import parse_spx_pdf,   validate_spx_df
from parsers.spx_xlsx_parser import parse_spx_xlsx,  validate_spx_xlsx_df, get_spx_xlsx_to_number
from parsers.tld_parser      import parse_tld_xls,   validate_tld_df
from parsers.fc_parser       import (
    parse_fc_xlsx, build_fc_index, validate_fc_df, CORE_LABELS, EXTENDED_LABELS,
)
from reconciler.engine import (
    reconcile_spx_tld, enrich_with_fc, match_key,
    build_order_groups, annotate_order_groups, collect_multi_tracking,
    build_unmatched_df, pair_unmatched, SIDE_COL, SIDE_SPX_ONLY, SIDE_TLD_ONLY,
)

# ── Page Config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Dispatch Reconciliation", page_icon="📦", layout="wide")
st.title("📦 Dispatch Reconciliation System")
st.caption("v3.0 — SPX (PDF + Excel) ↔ TLD Report  |  FC Export DO = ข้อมูลเสริม")
st.divider()

# ── CSS: bigger tabs ───────────────────────────────────────────────────────────
st.markdown("""
<style>
.stTabs [data-baseweb="tab-list"] { gap: 8px; }
.stTabs [data-baseweb="tab"] {
    font-size: 15px; font-weight: 600;
    padding: 10px 22px; border-radius: 6px 6px 0 0;
}
.stTabs [aria-selected="true"] { background-color: #1f4e79; color: white; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _get_cached(file_obj, key_suffix: str, parser_fn):
    k = f"{key_suffix}_{file_obj.name}_{file_obj.size}"
    if k not in st.session_state:
        file_obj.seek(0)
        st.session_state[k] = parser_fn(file_obj.read())
        file_obj.seek(0)
    return st.session_state[k]


def is_spx_excel(file_obj) -> bool:
    return file_obj.name.lower().endswith((".xlsx", ".xls"))


def parse_spx_file(file_obj) -> tuple[pd.DataFrame, bool, str, str]:
    """
    Parse SPX carrier file — dispatch ตามนามสกุล (.pdf → PDF parser, .xlsx → TO Excel parser)
    Returns (df, is_valid, error_msg, source_label)
    """
    if is_spx_excel(file_obj):
        df, to_no = _get_cached(file_obj, "spx_xlsx", parse_spx_xlsx)
        ok, err = validate_spx_xlsx_df(df)
        return df, ok, err, (to_no or "Excel")
    df = _get_cached(file_obj, "spx_pdf", parse_spx_pdf)
    ok, err = validate_spx_df(df)
    return df, ok, err, "PDF"


# ══════════════════════════════════════════════════════════════════════════════
# Step 1 — Upload
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("Step 1 — อัปโหลดไฟล์")

# ── SPX: multi-file, PDF + Excel ผสมกันได้ ────────────────────────────────────
st.markdown("**🚚 Carrier: SPX** *(เลือกได้หลายไฟล์ และผสม PDF กับ Excel ในคราวเดียวได้)*")
carrier_files = st.file_uploader(
    "SPX = .pdf (Shipment List) หรือ .xlsx (Transport Order)  —  Ctrl+click / Shift+click เพื่อเลือกหลายไฟล์",
    type=["pdf", "xlsx"],
    accept_multiple_files=True,
    key="carrier",
)
if carrier_files:
    _n_xlsx = sum(1 for f in carrier_files if is_spx_excel(f))
    _n_pdf  = len(carrier_files) - _n_xlsx
    _parts  = ([f"{_n_pdf} PDF"] if _n_pdf else []) + ([f"{_n_xlsx} Excel"] if _n_xlsx else [])
    st.success(f"✅ SPX: {len(carrier_files)} ไฟล์ ({' + '.join(_parts)})")

    if _n_xlsx:
        _to_nos = []
        for f in carrier_files:
            if not is_spx_excel(f):
                continue
            try:
                _to_nos.append(_get_cached(f, "spx_to_no", get_spx_xlsx_to_number) or f.name)
            except Exception:
                _to_nos.append(f.name)
        if _to_nos:
            st.info(f"🔖 TO Number ที่จะรวม: **{', '.join(_to_nos)}**")

st.markdown("---")

col_tld, col_fc = st.columns(2)

with col_tld:
    st.markdown("**📋 TLD Report** — *ระบบที่ใช้เทียบ (จำเป็น)*")
    tld_files = st.file_uploader(
        "TLD_Report .xls (เลือกได้หลายไฟล์)",
        type=["xls"],
        accept_multiple_files=True,
        key="tld_file",
    )
    if tld_files:
        st.success(f"✅ TLD: {len(tld_files)} ไฟล์")

with col_fc:
    st.markdown("**📊 FC Export DO** — *ข้อมูลเสริม (ไม่บังคับ)*")
    fc_file = st.file_uploader("FC Export DO = .xlsx", type=["xlsx"], key="fc_file")
    if fc_file:
        st.success("✅ FC Export DO — จะใช้เติมข้อมูล ไม่ได้เอามาเทียบ")
    else:
        st.caption("ถ้าไม่ใส่ ผลลัพธ์จะมีเฉพาะข้อมูลจาก TLD")

has_spx    = len(carrier_files) > 0
has_tld    = len(tld_files) > 0
has_fc     = fc_file is not None
both_ready = has_spx and has_tld

if carrier_files and not has_tld:
    st.info("⬆️ กรุณาอัปโหลด TLD Report (.xls) — เป็นระบบที่ใช้เทียบกับ SPX")


# ══════════════════════════════════════════════════════════════════════════════
# Step 2 — ตรวจสอบก่อน Reconcile
# ══════════════════════════════════════════════════════════════════════════════
fc_extended = False

if both_ready:
    st.divider()
    st.subheader("Step 2 — ตรวจสอบก่อน Reconcile")

    c_tld, c_fc = st.columns(2)

    with c_tld:
        st.markdown("**📋 TLD No. ที่จะ Reconcile**")
        _tld_nos = []
        for tf in tld_files:
            _cache_key = f"tld_no_{tf.name}_{tf.size}"
            if _cache_key not in st.session_state:
                tf.seek(0)
                _, _tno = parse_tld_xls(tf.read())
                tf.seek(0)
                st.session_state[_cache_key] = _tno
            _tld_nos.append(st.session_state[_cache_key] or tf.name)
        st.info(f"🔖 **{', '.join(_tld_nos)}**")
        st.caption("อ่านจากหัวไฟล์อัตโนมัติ — ไม่ต้องระบุเพิ่ม")

    with c_fc:
        st.markdown("**📊 ข้อมูลเสริมจาก FC Export DO**")
        if has_fc:
            fc_extended = st.checkbox(
                "แสดงข้อมูลเสริมแบบเต็ม",
                value=False,
                key="fc_extended",
                help="เพิ่ม: " + ", ".join(EXTENDED_LABELS),
            )
            _shown = CORE_LABELS + (EXTENDED_LABELS if fc_extended else [])
            st.caption(f"จะเติม {len(_shown)} คอลัมน์: {', '.join(_shown[:6])}"
                       + (f" … (+{len(_shown)-6})" if len(_shown) > 6 else ""))
            st.caption("จับคู่ด้วย 3PL Tracking No. ก่อน แล้วค่อย fallback เป็น Order No.")
        else:
            st.caption("— ไม่ได้อัปโหลด FC Export DO —")


# ══════════════════════════════════════════════════════════════════════════════
# Step 3 — Perspective + Reconcile Button
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
info_col, btn_col = st.columns([2, 1])

with info_col:
    st.markdown("**🚚 ยึด SPX เป็นตัวตั้ง** — เทียบว่าของที่ SPX รับไป ตรงกับที่ TLD บันทึกหรือไม่")

with btn_col:
    run_btn = st.button(
        "🔍 เริ่ม Reconcile",
        type="primary",
        disabled=not both_ready,
        width="stretch",
    )


# ══════════════════════════════════════════════════════════════════════════════
# Reconcile Logic
# ══════════════════════════════════════════════════════════════════════════════
if run_btn and both_ready:
    _prog = st.progress(0, text="📄 กำลังอ่าน SPX...")
    errors = []

    # ── Parse & concat multi-file SPX (PDF + Excel) ───────────────────────────
    spx_frames = []
    for cf in carrier_files:
        try:
            _df, ok, err, _ = parse_spx_file(cf)
            if ok:
                _df = _df.copy()
                _df["_source_file"] = cf.name
                spx_frames.append(_df)
            else:
                st.warning(f"⚠️ {cf.name}: {err}")
        except Exception as e:
            st.warning(f"⚠️ ไม่สามารถอ่าน {cf.name}: {e}")

    if not spx_frames:
        _prog.empty(); st.error("❌ ไม่สามารถอ่าน SPX ได้เลย"); st.stop()

    spx_df = pd.concat(spx_frames, ignore_index=True)
    for _c in ("order_sn", "tracking", "pickup_time", "to_number"):
        if _c not in spx_df.columns:
            spx_df[_c] = ""
        spx_df[_c] = spx_df[_c].fillna("").astype(str).str.strip()

    # Dedupe: Order SN ถ้ามี ไม่งั้นใช้ Tracking (ไฟล์ Excel ไม่มี Order SN)
    _n_before = len(spx_df)
    spx_df["_dedupe_key"] = match_key(spx_df, "order_sn", "tracking")
    spx_df = (spx_df[spx_df["_dedupe_key"].ne("")]
              .drop_duplicates(subset=["_dedupe_key"])
              .drop(columns=["_dedupe_key"])
              .reset_index(drop=True))
    _n_dup = _n_before - len(spx_df)

    _prog.progress(20, text=f"✅ SPX: {len(spx_df)} orders ({len(spx_frames)} ไฟล์)")

    # ── Reconcile vs TLD (concat all TLD files) ───────────────────────────────
    _prog.progress(35, text="📋 กำลังอ่าน TLD Report...")
    tld_frames, tld_nos = [], []
    for tf in tld_files:
        try:
            tf.seek(0)
            _tdf, _tno = parse_tld_xls(tf.read())
            tf.seek(0)
            ok, err = validate_tld_df(_tdf)
            if ok:
                tld_frames.append(_tdf)
                tld_nos.append(_tno)
            else:
                errors.append(f"TLD {tf.name}: {err}")
        except Exception as e:
            errors.append(f"TLD {tf.name} Error: {e}")

    result = None
    if tld_frames:
        tld_df_all = pd.concat(tld_frames, ignore_index=True)
        _prog.progress(55, text=f"✅ TLD: {len(tld_df_all)} rows — Reconcile...")
        try:
            result = reconcile_spx_tld(spx_df, tld_df_all, tld_no=", ".join(t for t in tld_nos if t))
        except Exception as e:
            errors.append(f"TLD Reconcile Error: {e}")

    if result is None:
        _prog.empty()
        for e in errors:
            st.error(f"❌ {e}")
        st.error("❌ Reconcile ไม่สำเร็จ"); st.stop()

    # ── Enrichment จาก FC Export DO ───────────────────────────────────────────
    fc_stats = None
    if has_fc:
        _prog.progress(70, text="📊 กำลังอ่าน FC Export DO (ไฟล์ใหญ่ อาจใช้เวลาสักครู่)...")
        try:
            fc_df = _get_cached(fc_file, "fc_enrich", parse_fc_xlsx)
            ok_fc, err_fc = validate_fc_df(fc_df)
            if not ok_fc:
                errors.append(f"FC: {err_fc}")
            else:
                fc_index = _get_cached(fc_file, "fc_index", lambda _b: build_fc_index(fc_df))
                labels = CORE_LABELS + (EXTENDED_LABELS if fc_extended else [])
                _prog.progress(85, text=f"📊 กำลังเติมข้อมูลจาก FC ({len(fc_df):,} rows)...")

                result.matched_df, n_m = enrich_with_fc(result.matched_df, fc_df, fc_index, labels)
                result.missing_in_wms_df, n_miss = enrich_with_fc(result.missing_in_wms_df, fc_df, fc_index, labels)
                result.extra_in_wms_df, n_extra = enrich_with_fc(result.extra_in_wms_df, fc_df, fc_index, labels)
                fc_stats = {
                    "fc_rows": len(fc_df), "n_cols": len(labels),
                    "matched": n_m, "missing": n_miss, "extra": n_extra,
                }
        except Exception as e:
            errors.append(f"FC Enrichment Error: {e}")

    # ── ตรวจออเดอร์ที่มีหลาย Tracking ─────────────────────────────────────────
    # สร้างกลุ่มจาก FC ถ้ามี (เห็นทั้งคลัง → รู้ว่ากล่องอื่นอยู่ Load ไหน)
    # ถ้าไม่มี FC ใช้ TLD ที่อัปโหลด (เห็นเฉพาะ Load เหล่านั้น)
    _prog.progress(95, text="🔎 กำลังตรวจออเดอร์ที่มีหลาย Tracking...")
    order_stats = None
    try:
        if has_fc and fc_stats:
            _fc_df = _get_cached(fc_file, "fc_enrich", parse_fc_xlsx)
            groups = _get_cached(fc_file, "fc_groups", lambda _b: build_order_groups(
                _fc_df["_order_no"], _fc_df["_tracking"], _fc_df["Truck Load No (FC)"]))
            scope = "FC Export DO (ทั้งคลัง)"
        else:
            groups = build_order_groups(
                tld_df_all["Order No"], tld_df_all["Tracking Number"],
                [result.filter_value] * len(tld_df_all))
            scope = "TLD ที่อัปโหลด (เห็นเฉพาะ Load เหล่านี้)"

        result.matched_df, st_m = annotate_order_groups(
            result.matched_df, groups, current_load=result.filter_value)
        result.extra_in_wms_df, st_e = annotate_order_groups(
            result.extra_in_wms_df, groups, current_load=result.filter_value)
        order_stats = {
            "scope": scope,
            "multi": st_m["multi"] + st_e["multi"],
            "cross_load": st_m["cross_load"] + st_e["cross_load"],
        }
    except Exception as e:
        errors.append(f"Order-group Error: {e}")

    _prog.progress(100, text="✅ เสร็จสิ้น")
    _prog.empty()

    for e in errors:
        st.error(f"❌ {e}")

    st.session_state["recon_results"] = {
        "result":    result,
        "spx_count": len(spx_df),
        "spx_files": [f.name for f in carrier_files],
        "n_dup":     _n_dup,
        "fc_stats":  fc_stats,
        "order_stats": order_stats,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Display Results
# ══════════════════════════════════════════════════════════════════════════════
if "recon_results" in st.session_state:
    _rs         = st.session_state["recon_results"]
    result      = _rs["result"]
    fc_stats    = _rs["fc_stats"]
    order_stats = _rs.get("order_stats")
    s           = result.summary

    st.success(f"✅ Reconcile สำเร็จ! — SPX {_rs['spx_count']} orders จาก {len(_rs['spx_files'])} ไฟล์")
    if _rs["n_dup"]:
        st.caption(f"ℹ️ ตัดรายการซ้ำ/ว่างออก {_rs['n_dup']} รายการก่อนเทียบ")

    if fc_stats:
        st.info(
            f"📊 **เติมข้อมูลจาก FC Export DO** ({fc_stats['fc_rows']:,} rows, {fc_stats['n_cols']} คอลัมน์) — "
            f"เติมได้ {fc_stats['matched'] + fc_stats['missing'] + fc_stats['extra']} รายการ"
        )
    st.divider()

    st.info(f"🔖 **{result.filter_key}** = `{result.filter_value or '(ทั้งไฟล์)'}`")

    # ── รายการที่ Match ไม่ได้ (รวมสองฝั่งเป็นตารางเดียว) ─────────────────────
    unmatched_df = build_unmatched_df(result.missing_in_wms_df, result.extra_in_wms_df)
    n_spx_only = s["missing_in_wms"]
    n_tld_only = s["extra_in_wms"]
    n_unmatched = n_spx_only + n_tld_only

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🚚 SPX", s["carrier_total"])
    c2.metric("🏭 TLD", s["wms_total"])
    c3.metric("✅ Match", s["matched"])
    c4.metric("⚠️ ไม่ Match", n_unmatched,
              delta=f"-{n_unmatched}" if n_unmatched else None, delta_color="inverse")

    pct = s["match_rate"]
    color = "green" if pct >= 95 else "orange" if pct >= 80 else "red"
    st.markdown(f"**Match Rate:** :{color}[**{pct}%**]")
    st.caption(f"วิธี match — Tracking: {s['matched_by_tracking']} | Order SN: {s['matched_by_orderkey']}")

    # สรุปทิศทางส่วนต่าง — SPX เป็นตัวตั้ง
    _diff = s["carrier_total"] - s["wms_total"]
    if n_unmatched == 0:
        st.success("🎉 ตรงกันทั้งหมด — ทุกรายการที่ SPX รับ มีบันทึกใน TLD ครบ")
    elif _diff > 0:
        st.warning(f"⚠️ **SPX มากกว่า TLD {_diff} รายการ** — SPX รับของที่ TLD ไม่มีบันทึก")
    elif _diff < 0:
        st.warning(f"⚠️ **SPX น้อยกว่า TLD {abs(_diff)} รายการ** — TLD ปล่อยของที่ SPX ไม่ได้รับ")
    else:
        st.warning(f"⚠️ **จำนวนเท่ากันแต่จับคู่ไม่ได้ {n_spx_only} รายการ** — น่าจะเป็นของชิ้นเดียวกันที่ Tracking ไม่ตรง")

    # ── วิเคราะห์คู่ที่น่าจะ map ไม่ติด ────────────────────────────────────────
    pairs_df, pair_stats = pair_unmatched(result.missing_in_wms_df, result.extra_in_wms_df)
    n_pairs = len(pairs_df)
    if n_pairs:
        st.error(
            f"🔍 พบ **{n_pairs} คู่** ที่น่าจะเป็นของชิ้นเดียวกันแต่จับคู่ไม่ติด "
            f"(ค้างฝั่ง SPX {pair_stats['spx_only']} / ฝั่ง TLD {pair_stats['tld_only']}) — ดูแท็บ Mapping"
        )

    # ── ออเดอร์ที่มีหลาย Tracking ─────────────────────────────────────────────
    multi_df, n_cross = collect_multi_tracking(
        [result.matched_df, result.extra_in_wms_df] if order_stats else []
    )
    n_multi = len(multi_df)

    if order_stats:
        if n_multi:
            st.warning(
                f"⚠️ พบ **{n_multi} รายการ** ที่ออเดอร์เดียวกันถูกแยกเป็นหลาย Tracking"
                + (f" — ในนั้น **{n_cross} รายการ** มีกล่องอื่นอยู่คนละ Truck Load" if n_cross else "")
            )
        st.caption(f"ตรวจออเดอร์แยกกล่องจากขอบเขต: {order_stats['scope']}")

    st.divider()

    # ── Tabs ──────────────────────────────────────────────────────────────────
    _tab_match, _tab_unmatch, _tab_pairs, _tab_multi = st.tabs([
        f"✅ Match ({s['matched']})",
        f"⚠️ ไม่ Match ({n_unmatched})",
        f"🔍 Mapping ไม่ติด ({n_pairs})",
        f"🔗 ออเดอร์หลาย Tracking ({n_multi})",
    ])

    with _tab_match:
        if not result.matched_df.empty:
            st.dataframe(result.matched_df, width="stretch", hide_index=True)
        else:
            st.info("ไม่มีรายการ Match")

    with _tab_unmatch:
        st.caption(
            f"**{SIDE_SPX_ONLY}** = SPX รับไปแต่ TLD ไม่มีบันทึก  |  "
            f"**{SIDE_TLD_ONLY}** = TLD ปล่อยออกแต่ SPX ไม่ได้รับ"
        )
        if unmatched_df.empty:
            st.success("🎉 ไม่มีรายการค้าง — จับคู่ได้ครบทั้งสองฝั่ง")
        else:
            _sides = st.multiselect(
                "กรองฝั่ง", [SIDE_SPX_ONLY, SIDE_TLD_ONLY],
                default=[SIDE_SPX_ONLY, SIDE_TLD_ONLY], key="unmatch_sides",
            )
            _view = unmatched_df[unmatched_df[SIDE_COL].isin(_sides)] if _sides else unmatched_df
            st.dataframe(_view, width="stretch", hide_index=True)
            st.caption(f"{SIDE_SPX_ONLY}: {n_spx_only} | {SIDE_TLD_ONLY}: {n_tld_only}")

    with _tab_pairs:
        st.caption(
            "ถ้าค้างทั้งสองฝั่งพร้อมกัน มักไม่ใช่ของหายจริง แต่เป็นของชิ้นเดียวกันที่ Tracking "
            "เขียนไม่ตรงกันจนจับคู่ไม่ติด — ระบบลองจับคู่ให้จาก Order SN และความคล้ายของ Tracking"
        )
        if pairs_df.empty:
            if pair_stats["spx_only"] and pair_stats["tld_only"]:
                st.info(
                    f"ค้างทั้งสองฝั่ง (SPX {pair_stats['spx_only']} / TLD {pair_stats['tld_only']}) "
                    "แต่จับคู่ไม่ได้ — น่าจะเป็นคนละรายการจริงๆ"
                )
            else:
                st.success("🎉 ไม่มีรายการค้างทั้งสองฝั่งพร้อมกัน")
        else:
            st.dataframe(pairs_df, width="stretch", hide_index=True)
            _left_spx = pair_stats["spx_only"] - n_pairs
            _left_tld = pair_stats["tld_only"] - n_pairs
            st.caption(
                f"จับคู่ได้ {n_pairs} คู่ | เหลือค้างจริง — SPX {_left_spx} / TLD {_left_tld}"
            )
        if pair_stats["scan_skipped"]:
            st.caption("⚠️ รายการค้างเยอะเกิน 500 — ข้ามการเทียบความคล้ายของ Tracking ทีละคู่")

    with _tab_multi:
        st.caption(
            "ออเดอร์เดียวถูกแยกเป็นหลายกล่อง/หลาย Tracking — "
            "ถ้ามีค่าในคอลัมน์ **Load อื่นในออเดอร์** แปลว่ากล่องที่เหลือถูกปล่อยไปกับ Load ใบอื่น"
        )
        if not order_stats:
            st.info("ไม่ได้ตรวจ — ต้องอัปโหลด FC Export DO หรือ TLD เพื่อดูโครงสร้างออเดอร์")
        elif multi_df.empty:
            st.success("🎉 ทุกออเดอร์มี Tracking เดียว ไม่มีการแยกกล่อง")
        else:
            if n_cross:
                st.error(f"🔴 {n_cross} รายการ มีกล่องอื่นอยู่คนละ Truck Load — ต้องตรวจสอบ")
            st.dataframe(multi_df, width="stretch", hide_index=True)

    # ── Export Excel (Minimal Calibri style + auto-fit) ───────────────────────
    def _write_sheet(writer, df: pd.DataFrame, sheet_name: str):
        """Write DataFrame to sheet with Calibri 11pt, styled header, auto-fit columns."""
        if df.empty:
            df = df.copy()  # write empty with headers
        df.to_excel(writer, sheet_name=sheet_name, index=False, startrow=1, header=False)

        wb  = writer.book
        ws  = writer.sheets[sheet_name]

        # ── Formats ──────────────────────────────────────────────────────────
        base = {"font_name": "Calibri", "font_size": 11}
        fmt_hdr = wb.add_format({**base,
            "bold": True, "font_color": "#FFFFFF",
            "bg_color": "#1F4E79", "border": 1,
            "align": "center", "valign": "vcenter", "text_wrap": True})
        fmt_data = wb.add_format({**base,
            "border": 0, "bottom": 1, "border_color": "#D9D9D9",
            "valign": "vcenter"})
        fmt_data_alt = wb.add_format({**base,
            "border": 0, "bottom": 1, "border_color": "#D9D9D9",
            "bg_color": "#F7FBFF", "valign": "vcenter"})

        # ── Write headers (row 0) ─────────────────────────────────────────────
        for col_idx, col_name in enumerate(df.columns):
            ws.write(0, col_idx, col_name, fmt_hdr)

        # ── Write data rows with alternating fill ─────────────────────────────
        for row_idx, row in enumerate(df.itertuples(index=False), start=1):
            fmt = fmt_data_alt if row_idx % 2 == 0 else fmt_data
            for col_idx, val in enumerate(row):
                ws.write(row_idx, col_idx, "" if val is None or (isinstance(val, float) and pd.isna(val)) else val, fmt)

        # ── Auto-fit column widths (based on header + data max length) ────────
        for col_idx, col_name in enumerate(df.columns):
            header_len = len(str(col_name))
            if df.empty:
                data_len = 0
            else:
                data_len = df.iloc[:, col_idx].astype(str).str.len().max()
                data_len = int(data_len) if not pd.isna(data_len) else 0
            col_width = min(max(header_len, data_len) + 2, 45)  # cap at 45
            ws.set_column(col_idx, col_idx, col_width)

        # ── Freeze top row ────────────────────────────────────────────────────
        ws.freeze_panes(1, 0)
        ws.set_row(0, 22)  # header row height

    st.divider()
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        _write_sheet(writer, result.matched_df, "Match")
        _write_sheet(writer, unmatched_df,      "ไม่ Match")
        if not pairs_df.empty:
            _write_sheet(writer, pairs_df, "Mapping ไม่ติด")
        if not multi_df.empty:
            _write_sheet(writer, multi_df, "ออเดอร์หลาย Tracking")
        # เก็บรายละเอียดเต็มของแต่ละฝั่งไว้ให้ตรวจย้อนหลัง
        _write_sheet(writer, result.missing_in_wms_df, "รายละเอียด_SPX เท่านั้น")
        _write_sheet(writer, result.extra_in_wms_df,   "รายละเอียด_TLD เท่านั้น")
        _write_sheet(writer, pd.DataFrame({
            "Metric": ["SPX ทั้งหมด", "TLD ทั้งหมด", "Match", "ไม่ Match (รวม)",
                       "มีใน SPX ไม่มีใน TLD", "มีใน TLD ไม่มีใน SPX", "ส่วนต่าง (SPX - TLD)",
                       "Match Rate", "TLD No.", "ข้อมูลเสริมจาก FC",
                       "คู่ที่น่าจะ Mapping ไม่ติด",
                       "ออเดอร์หลาย Tracking", "กล่องอยู่คนละ Load", "ขอบเขตที่ตรวจ"],
            "Value":  [s["carrier_total"], s["wms_total"], s["matched"], n_unmatched,
                       n_spx_only, n_tld_only, _diff,
                       f"{s['match_rate']}%",
                       result.filter_value or "(ทั้งไฟล์)",
                       f"{fc_stats['n_cols']} คอลัมน์" if fc_stats else "ไม่ได้ใช้",
                       n_pairs, n_multi, n_cross,
                       order_stats["scope"] if order_stats else "-"],
        }), "Summary")
    buf.seek(0)

    st.download_button(
        "📥 Download Excel Report",
        buf,
        file_name=f"reconciliation_SPX_{result.filter_value or 'all'}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )


elif not carrier_files and not has_tld:
    st.markdown("""
### วิธีใช้งาน v3.0
1. **อัปโหลด SPX** — เลือกได้หลายไฟล์พร้อมกัน (Ctrl+click) และผสม **PDF** กับ **Excel (Transport Order)** ได้
   ทุกไฟล์จะถูกรวมเป็นชุดเดียวก่อน Reconcile (ตัดรายการซ้ำอัตโนมัติ)
2. **อัปโหลด TLD Report** (.xls) — ระบบที่ใช้เทียบกับ SPX *(จำเป็น)*
3. **อัปโหลด FC Export DO** (.xlsx) — *ไม่บังคับ* ใช้เติมข้อมูลที่ TLD ไม่มี:
   เลขที่ Order · วันที่ Order · Handover date/time · Brand · จำนวนชิ้น · สถานะ Cancel ฯลฯ
4. กด **Reconcile**

---
#### ผลลัพธ์ 4 แท็บ
| แท็บ | บอกอะไร |
|---|---|
| ✅ **Match** | SPX รับ และ TLD มีบันทึก ตรงกัน |
| ⚠️ **ไม่ Match** | รายการค้าง แยกเป็น *มีใน SPX ไม่มีใน TLD* กับ *มีใน TLD ไม่มีใน SPX* |
| 🔍 **Mapping ไม่ติด** | ค้างทั้งสองฝั่งพร้อมกัน = มักเป็นของชิ้นเดียวกันที่ Tracking เขียนไม่ตรง |
| 🔗 **ออเดอร์หลาย Tracking** | ออเดอร์เดียวถูกแยกหลายกล่อง — เตือนถ้ากล่องอื่นอยู่คนละ Load |

> - 🚚 ยึด **SPX เป็นตัวตั้ง** — สรุปว่า SPX มากกว่าหรือน้อยกว่า TLD กี่รายการ
> - ℹ️ ไฟล์ SPX แบบ **Excel** มีเฉพาะ Tracking Number (ไม่มี Order SN) — match ด้วย Tracking
> - 📊 FC จับคู่ด้วย **3PL Transport Tracking No.** ก่อน แล้ว fallback เป็น Order No.
""")
