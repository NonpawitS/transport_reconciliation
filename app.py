"""
Dispatch Reconciliation System — v1.3
Goal: ตรวจสอบว่า สิ่งที่ 3PL มารับ ตรงกับสิ่งที่คลังปล่อยออกไป
รองรับ: SPX + FC | SPX + WDCS | SPX + FC + WDCS (พร้อมกัน)
"""
import io
import streamlit as st
import pandas as pd

from parsers.spx_parser import parse_spx_pdf, validate_spx_df
from parsers.wdcs_parser import parse_wdcs_txt, validate_wdcs_df
from parsers.fc_parser import parse_fc_xlsx, validate_fc_df, get_fc_load_numbers
from reconciler.engine import reconcile_spx_wdcs, reconcile_spx_fc, find_matching_transports

# ── Page Config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Dispatch Reconciliation", page_icon="📦", layout="wide")
st.title("📦 Dispatch Reconciliation System")
st.caption("โปรแกรมสำหรับตรวจสอบว่า สิ่งที่ 3PL มารับ = สิ่งที่คลังปล่อยออกไป — v1.3")
st.divider()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _get_cached(file_obj, key_suffix: str, parser_fn):
    """Cache parsed dataframe in session_state by filename+size."""
    k = f"{key_suffix}_{file_obj.name}_{file_obj.size}"
    if k not in st.session_state:
        file_obj.seek(0)
        st.session_state[k] = parser_fn(file_obj.read())
        file_obj.seek(0)
    return st.session_state[k]


def get_cached_fc(fc_file) -> pd.DataFrame:
    return _get_cached(fc_file, "fc_full", lambda b: parse_fc_xlsx(b, truck_load_no=""))


def get_cached_wdcs(wdcs_file) -> pd.DataFrame:
    return _get_cached(wdcs_file, "wdcs_full", lambda b: parse_wdcs_txt(b, transport_no=""))


# ── Step 1: File Upload ───────────────────────────────────────────────────────
st.subheader("Step 1 — อัปโหลดไฟล์")
st.caption("อัปโหลด FC และ/หรือ WDCS ได้พร้อมกัน เพื่อ Reconcile ทีเดียว")

col_carrier, col_fc, col_wdcs = st.columns(3)

carrier_type = None

with col_carrier:
    st.markdown("**🚚 Carrier File** *(Master)*")
    carrier_file = st.file_uploader("SPX = .pdf", type=["pdf", "xlsx", "xls"], key="carrier")
    if carrier_file:
        ext = carrier_file.name.split(".")[-1].lower()
        if ext == "pdf":
            st.success("✅ SPX (PDF)")
            carrier_type = "SPX"
        elif ext in ("xlsx", "xls"):
            st.info("ℹ️ Kerry (Excel) — Phase 2")
            carrier_type = "Kerry"
        st.session_state["carrier_type"] = carrier_type
    else:
        carrier_type = st.session_state.get("carrier_type")

with col_fc:
    st.markdown("**🏭 FC Export DO** *(optional)*")
    fc_file = st.file_uploader("FC = .xlsx", type=["xlsx", "xls"], key="fc_file")
    if fc_file:
        st.success("✅ FC (Excel)")

with col_wdcs:
    st.markdown("**🏭 WDCS Export DO** *(optional)*")
    wdcs_file = st.file_uploader("WDCS = .txt", type=["txt"], key="wdcs_file")
    if wdcs_file:
        st.success("✅ WDCS (Tab-delimited TXT)")

has_spx = carrier_file is not None and carrier_type == "SPX"
has_fc = fc_file is not None
has_wdcs = wdcs_file is not None
any_wms = has_fc or has_wdcs
both_ready = has_spx and any_wms


# ── Step 2: Filters ────────────────────────────────────────────────────────────
fc_filter = ""
wdcs_filter = ""

if both_ready:
    st.divider()
    st.subheader("Step 2 — ระบุ Transport / Load No.")

    filter_cols = st.columns(2) if (has_fc and has_wdcs) else [st.container()]

    # ── FC Filter ─────────────────────────────────────────────────────────────
    if has_fc:
        with filter_cols[0]:
            st.markdown("**📦 FC — Truck Load No.**")
            input_mode_fc = st.radio(
                "วิธีระบุ (FC)",
                options=["📷 Scan / พิมพ์เอง", "🔍 Dropdown", "📦 ทั้งไฟล์"],
                horizontal=True,
                key="input_mode_fc",
            )

            if input_mode_fc == "📷 Scan / พิมพ์เอง":
                fc_filter = st.text_input(
                    "Truck Load No. (FC)", key="fc_manual",
                    placeholder="เช่น TLD2603001522",
                )
                if fc_filter:
                    st.caption(f"🔖 FC: **{fc_filter}**")

            elif input_mode_fc == "🔍 Dropdown":
                fc_tld_key = f"fc_tld_{fc_file.name}_{fc_file.size}"
                if fc_tld_key not in st.session_state:
                    if st.button("โหลดรายการ TLD", key="fc_load_btn"):
                        with st.spinner("กำลังอ่าน FC..."):
                            try:
                                fc_file.seek(0)
                                tlds = get_fc_load_numbers(fc_file.read())
                                fc_file.seek(0)
                                st.session_state[fc_tld_key] = tlds
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ {e}")
                    st.caption("⚠️ อาจใช้เวลา 5–15 วินาที")
                if fc_tld_key in st.session_state:
                    tlds = st.session_state[fc_tld_key]
                    if tlds:
                        srch = st.text_input("กรอง TLD", key="fc_tld_srch", placeholder="TLD2603")
                        opts = [t for t in tlds if srch.lower() in t.lower()] if srch else tlds
                        if opts:
                            fc_filter = st.selectbox("TLD", opts, key="fc_tld_sel", label_visibility="collapsed") or ""
                            if fc_filter:
                                st.caption(f"🔖 FC: **{fc_filter}**")
                        else:
                            st.warning(f"ไม่พบ '{srch}'")
                    else:
                        st.warning("ไม่พบ TLD — จะใช้ทั้งไฟล์")

            else:  # ทั้งไฟล์
                fc_filter = ""
                st.caption("📦 Reconcile ทั้งไฟล์ FC")

            # FC debug expander
            with st.expander("🔬 ตรวจสอบไฟล์ FC (debug)"):
                if st.button("วิเคราะห์ไฟล์ FC", key="fc_inspect"):
                    with st.spinner("กำลังอ่าน..."):
                        import io as _io, pandas as _pd
                        fc_file.seek(0)
                        _raw = _pd.read_excel(_io.BytesIO(fc_file.read()), header=1, dtype=str, nrows=5)
                        fc_file.seek(0)
                        _raw.columns = [str(c).strip() for c in _raw.columns]
                        fc_full = get_cached_fc(fc_file)
                    st.write("**Raw columns:**", list(_raw.columns))
                    st.write(f"**Rows parsed:** {len(fc_full)}")
                    for col, label in [
                        ("Order no", "Order no non-null"),
                        ("3PL Transport Tracking No", "3PL Tracking non-null"),
                        ("Truck Load No", "Truck Load No non-null"),
                    ]:
                        if col in fc_full.columns:
                            _s = fc_full[col].dropna().str.strip().replace("", _pd.NA).dropna()
                            st.write(f"**{label}:** {len(_s)} | Sample: {_s.head(3).tolist()}")
                    if "Carrier" in fc_full.columns:
                        _car = fc_full["Carrier"].dropna().str.strip().replace("", _pd.NA).dropna()
                        st.write(f"**Carrier unique:** {_car.value_counts().head(5).to_dict()}")
                    if "Document Type Name" in fc_full.columns:
                        _dt = fc_full["Document Type Name"].dropna().str.strip().replace("", _pd.NA).dropna()
                        st.write(f"**Document Type Name:**")
                        st.dataframe(_dt.value_counts().head(10).reset_index(), use_container_width=True, hide_index=True)

    # ── WDCS Filter ───────────────────────────────────────────────────────────
    if has_wdcs:
        with filter_cols[1 if has_fc else 0]:
            st.markdown("**📄 WDCS — Transport No.**")
            input_mode_wdcs = st.radio(
                "วิธีระบุ (WDCS)",
                options=["📷 Scan / พิมพ์เอง", "🔍 Dropdown"],
                horizontal=True,
                key="input_mode_wdcs",
            )

            if input_mode_wdcs == "📷 Scan / พิมพ์เอง":
                wdcs_filter = st.text_input(
                    "Transport No. (WDCS)", key="wdcs_manual",
                    placeholder="เช่น 3260001318",
                )
                if wdcs_filter:
                    st.caption(f"🔖 WDCS: **{wdcs_filter}**")

            else:  # Dropdown
                wdcs_cache_key = f"wdcs_transport_{carrier_file.name}_{wdcs_file.name}"
                if wdcs_cache_key not in st.session_state:
                    if st.button("🔍 วิเคราะห์ WDCS", key="wdcs_analyze_btn"):
                        with st.spinner("กำลังวิเคราะห์..."):
                            try:
                                carrier_file.seek(0)
                                spx_df_tmp = parse_spx_pdf(carrier_file.read())
                                carrier_file.seek(0)
                                carrier_keys = set(spx_df_tmp["order_sn"].dropna().str.strip().replace("", pd.NA).dropna())
                                wdcs_full = get_cached_wdcs(wdcs_file)
                                matches = find_matching_transports(carrier_keys, wdcs_full, "Transport_No", "Web Order")
                                st.session_state[wdcs_cache_key] = matches
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ {e}")
                    st.caption("⚠️ อาจใช้เวลา 5–15 วินาที")

                if wdcs_cache_key in st.session_state:
                    matches = st.session_state[wdcs_cache_key]
                    n_match = sum(1 for m in matches if m["match_count"] > 0)
                    if n_match:
                        st.success(f"✅ พบ {n_match} Transport ที่ตรงกับ SPX")
                    all_labels = [
                        f"{m['transport_no']}  {'✅' if m['match_count'] > 0 else '  '} {m['match_count']} match"
                        for m in matches
                    ]
                    all_values = [m["transport_no"] for m in matches]
                    srch_w = st.text_input("กรอง Transport", key="wdcs_srch", placeholder="3260")
                    if srch_w.strip():
                        labels_f = [l for l, v in zip(all_labels, all_values) if srch_w.lower() in v.lower()]
                        values_f = [v for v in all_values if srch_w.lower() in v.lower()]
                    else:
                        # Show matched first
                        labels_f = [l for l, m in zip(all_labels, matches) if m["match_count"] > 0] + \
                                   [l for l, m in zip(all_labels, matches) if m["match_count"] == 0]
                        values_f = [v for v, m in zip(all_values, matches) if m["match_count"] > 0] + \
                                   [v for v, m in zip(all_values, matches) if m["match_count"] == 0]
                    if labels_f:
                        sel_lbl = st.selectbox("Transport No.", labels_f, key="wdcs_sel", label_visibility="collapsed")
                        wdcs_filter = sel_lbl.split(" ")[0] if sel_lbl else ""
                        if wdcs_filter:
                            st.caption(f"🔖 WDCS: **{wdcs_filter}**")
                    if st.button("🔄 โหลดใหม่", key="wdcs_refresh"):
                        del st.session_state[wdcs_cache_key]
                        st.rerun()

elif carrier_file and not has_spx:
    st.warning("⚠️ Phase นี้รองรับ SPX (PDF) — Kerry จะพร้อมใน Phase 2")

elif carrier_file and not any_wms:
    st.info("⬆️ กรุณา upload FC (.xlsx) และ/หรือ WDCS (.txt)")


# ── Step 3: Reconcile Button ──────────────────────────────────────────────────
st.divider()
run_btn = st.button(
    "🔍 เริ่ม Reconcile",
    type="primary",
    disabled=not both_ready,
    use_container_width=True,
    help="Reconcile FC และ/หรือ WDCS กับ SPX",
)


# ── Reconcile Logic (runs only when button clicked) ───────────────────────────
if run_btn and both_ready:
    _prog = st.progress(0, text="📄 กำลังอ่าน SPX...")

    # Parse SPX
    carrier_file.seek(0)
    try:
        spx_df = parse_spx_pdf(carrier_file.read())
        ok, err = validate_spx_df(spx_df)
        if not ok:
            _prog.empty(); st.error(f"❌ SPX: {err}"); st.stop()
    except Exception as e:
        _prog.empty(); st.error(f"❌ ไม่สามารถอ่าน SPX: {e}"); st.stop()
    _prog.progress(20, text=f"✅ SPX: {len(spx_df)} orders")

    _fc_result = None
    _wdcs_result = None
    errors = []

    # ── Reconcile FC ──────────────────────────────────────────────────────────
    if has_fc:
        _prog.progress(30, text="📊 กำลังอ่าน FC...")
        try:
            fc_df_full = get_cached_fc(fc_file)
            if fc_filter and fc_filter.strip() and "Truck Load No" in fc_df_full.columns:
                fc_df = fc_df_full[fc_df_full["Truck Load No"].astype(str).str.strip() == fc_filter.strip()].copy()
            else:
                fc_df = fc_df_full.copy()
            ok2, err2 = validate_fc_df(fc_df, fc_filter)
            if not ok2:
                errors.append(f"FC: {err2}")
            else:
                _prog.progress(55, text=f"✅ FC: {len(fc_df)} rows — Reconcile...")
                _fc_result = reconcile_spx_fc(spx_df, fc_df, truck_load_no=fc_filter)
        except Exception as e:
            errors.append(f"FC Error: {e}")

    # ── Reconcile WDCS ────────────────────────────────────────────────────────
    if has_wdcs:
        _prog.progress(65, text="📊 กำลังอ่าน WDCS...")
        try:
            wdcs_file.seek(0)
            wdcs_df = parse_wdcs_txt(wdcs_file.read(), transport_no=wdcs_filter)
            wdcs_file.seek(0)
            ok3, err3 = validate_wdcs_df(wdcs_df, wdcs_filter)
            if not ok3:
                errors.append(f"WDCS: {err3}")
            else:
                _prog.progress(85, text=f"✅ WDCS: {len(wdcs_df)} rows — Reconcile...")
                _wdcs_result = reconcile_spx_wdcs(spx_df, wdcs_df, transport_no=wdcs_filter)
        except Exception as e:
            errors.append(f"WDCS Error: {e}")

    _prog.progress(100, text="✅ เสร็จสิ้น")
    _prog.empty()

    for e in errors:
        st.error(f"❌ {e}")

    # ── Save results to session_state so they survive reruns (AI button clicks) ─
    if _fc_result or _wdcs_result:
        st.session_state["recon_results"] = {
            "fc_result": _fc_result,
            "wdcs_result": _wdcs_result,
            "fc_filter": fc_filter,
            "wdcs_filter": wdcs_filter,
        }
    else:
        st.stop()


# ── Display Results (loaded from session_state — survives AI button reruns) ───
if "recon_results" in st.session_state:
    _rs = st.session_state["recon_results"]
    fc_result = _rs["fc_result"]
    wdcs_result = _rs["wdcs_result"]
    _fc_filter = _rs["fc_filter"]
    _wdcs_filter = _rs["wdcs_filter"]

    st.success("✅ Reconcile สำเร็จ!")
    st.divider()

    # ── CSS: bigger tabs ──────────────────────────────────────────────────────
    st.markdown("""
    <style>
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        font-size: 15px;
        font-weight: 600;
        padding: 10px 22px;
        border-radius: 6px 6px 0 0;
    }
    .stTabs [aria-selected="true"] { background-color: #1f4e79; color: white; }
    </style>
    """, unsafe_allow_html=True)

    # ── Compute Combined sets (needed for tab labels) ─────────────────────────
    spx_col = "Order SN (SPX)"
    has_both = fc_result is not None and wdcs_result is not None

    if has_both:
        fc_matched_sns  = set(fc_result.matched_df["SPX Order SN"].dropna())   if not fc_result.matched_df.empty  and "SPX Order SN" in fc_result.matched_df.columns  else set()
        wdcs_matched_sns = set(wdcs_result.matched_df["Web Order"].dropna())   if not wdcs_result.matched_df.empty and "Web Order"    in wdcs_result.matched_df.columns else set()
        fc_miss_sns     = set(fc_result.missing_in_wms_df[spx_col])            if spx_col in fc_result.missing_in_wms_df.columns   else set()
        wdcs_miss_sns   = set(wdcs_result.missing_in_wms_df[spx_col])          if spx_col in wdcs_result.missing_in_wms_df.columns else set()

        # 4 groups
        # "ยืนยันทั้ง 2 ระบบ" = all rows confirmed by any WMS (FC matched + WDCS matched รวมกัน)
        n_both_confirmed    = (len(fc_result.matched_df) if not fc_result.matched_df.empty else 0) + \
                              (len(wdcs_result.matched_df) if not wdcs_result.matched_df.empty else 0)
        grp_fc_only         = fc_matched_sns  - wdcs_matched_sns               # matched FC, missing WDCS
        grp_wdcs_only       = wdcs_matched_sns - fc_matched_sns                # matched WDCS, missing FC
        grp_missing_both    = fc_miss_sns     & wdcs_miss_sns                  # missing from both ← critical

    # ── Build tab list: Combined first when both available ────────────────────
    tab_labels = []
    tab_slots  = []   # ("combined"|"FC"|"WDCS", result_or_None, filter_str)

    if has_both:
        tab_labels.append(f"🔀 Combined")
        tab_slots.append(("combined", None, ""))

    if fc_result:
        s_fc = fc_result.summary
        tab_labels.append(f"🏭 FC  ✅ {s_fc['matched']}  ⚠️ {s_fc['missing_in_wms']}")
        tab_slots.append(("FC", fc_result, _fc_filter))

    if wdcs_result:
        s_w = wdcs_result.summary
        tab_labels.append(f"📄 WDCS  ✅ {s_w['matched']}  ⚠️ {s_w['missing_in_wms']}")
        tab_slots.append(("WDCS", wdcs_result, _wdcs_filter))

    tabs = st.tabs(tab_labels)

    # ── Helper: render single-system result ───────────────────────────────────
    def render_result(tab, label: str, result, filter_val: str):
        s = result.summary
        with tab:
            if filter_val:
                st.info(f"🔖 **{result.filter_key}** = `{result.filter_value}`")
            else:
                st.warning("⚠️ ไม่ได้ระบุ Filter — ใช้ข้อมูลทั้งหมด")

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("🚚 SPX", s["carrier_total"])
            c2.metric("🏭 WMS", s["wms_total"])
            c3.metric("✅ Matched", s["matched"])
            c4.metric("⚠️ Missing WMS", s["missing_in_wms"], delta_color="inverse")
            c5.metric("⚠️ Extra WMS", s["extra_in_wms"], delta_color="inverse")

            pct = s["match_rate"]
            color = "green" if pct >= 95 else "orange" if pct >= 80 else "red"
            st.markdown(f"**Match Rate:** :{color}[**{pct}%**]")

            if label == "FC" and "matched_by_tracking" in s:
                st.caption(f"Match method — Tracking: {s['matched_by_tracking']} | Order SN: {s['matched_by_orderkey']}")

            # FC: TLD breakdown (no filter)
            if label == "FC" and not filter_val and not result.matched_df.empty:
                if "Truck Load No" in result.matched_df.columns:
                    _ld = result.matched_df.copy()
                    _ld["Truck Load No"] = _ld["Truck Load No"].astype(str).str.strip()
                    _ld = _ld[_ld["Truck Load No"].replace("nan", "").ne("")]
                    if not _ld.empty:
                        st.divider()
                        st.subheader("📦 สรุปตาม Truck Load No.")
                        _agg: dict = {"Order no": "count"}
                        if "Total Box" in _ld.columns:
                            _ld["Total Box"] = pd.to_numeric(_ld["Total Box"], errors="coerce")
                            _agg["Total Box"] = "sum"
                        _tbl = _ld.groupby("Truck Load No", sort=False).agg(_agg).reset_index()
                        _tbl.columns = (["Truck Load No", "Matched Orders", "Total Box"] if "Total Box" in _agg
                                        else ["Truck Load No", "Matched Orders"])
                        _tbl = _tbl.sort_values("Truck Load No", ascending=False, key=lambda s: s.astype(str))
                        st.dataframe(_tbl, use_container_width=True, hide_index=True)

            st.divider()
            dt1, dt2, dt3 = st.tabs([
                f"✅ Matched ({s['matched']})",
                f"⚠️ Missing in WMS ({s['missing_in_wms']})",
                f"⚠️ Extra in WMS ({s['extra_in_wms']})",
            ])
            with dt1:
                if not result.matched_df.empty:
                    st.dataframe(result.matched_df, use_container_width=True, hide_index=True)
                else:
                    st.info("ไม่มีรายการ Match")
            with dt2:
                st.caption("3PL รับไปแต่ WMS ไม่มีบันทึก")
                if not result.missing_in_wms_df.empty:
                    st.dataframe(result.missing_in_wms_df, use_container_width=True, hide_index=True)
                else:
                    st.success("🎉 ครบถ้วน")
            with dt3:
                st.caption("WMS ปล่อยออกแต่ 3PL ไม่ได้รับ")
                if not result.extra_in_wms_df.empty:
                    st.dataframe(result.extra_in_wms_df, use_container_width=True, hide_index=True)
                else:
                    st.success("🎉 ครบถ้วน")

    # ── Render each tab ────────────────────────────────────────────────────────
    for tab_obj, (slot_type, result, fv) in zip(tabs, tab_slots):
        if slot_type == "combined":
            # ── Combined: 4-group view ─────────────────────────────────────
            with tab_obj:
                st.markdown("### 🔀 ภาพรวมจาก FC + WDCS")
                st.caption(
                    "แต่ละ order ของ SPX ถูกแบ่งเป็น 4 กลุ่มตามว่าพบใน FC และ/หรือ WDCS หรือไม่"
                )

                # ── Metric row ────────────────────────────────────────────
                m1, m2, m3, m4 = st.columns(4)
                m1.metric(
                    "✅✅ ยืนยันทั้ง 2 ระบบ",
                    n_both_confirmed,
                    help="FC matched + WDCS matched รวมกัน — คลังมีบันทึกครบ",
                )
                m2.metric(
                    "🟡 พบ FC / ไม่พบ WDCS",
                    len(grp_fc_only),
                    help="FC มีบันทึก แต่ WDCS ไม่มี — ควรตรวจสอบ WDCS",
                    delta_color="off",
                )
                m3.metric(
                    "🟡 พบ WDCS / ไม่พบ FC",
                    len(grp_wdcs_only),
                    help="WDCS มีบันทึก แต่ FC ไม่มี — ควรตรวจสอบ FC",
                    delta_color="off",
                )
                m4.metric(
                    "🔴 ไม่พบทั้ง 2 ระบบ",
                    len(grp_missing_both),
                    help="หายจากทั้ง FC และ WDCS — ตรวจสอบเร่งด่วน",
                    delta_color="inverse",
                )

                st.divider()

                # ── Explanation ───────────────────────────────────────────
                st.markdown("""
| สถานะ | ความหมาย | ต้องทำอะไร |
|---|---|---|
| ✅✅ ยืนยันทั้ง 2 ระบบ | FC และ WDCS ต่างมีบันทึก Order นี้ | ไม่ต้องทำอะไร |
| 🟡 พบ FC / ไม่พบ WDCS | FC มี แต่ WDCS ไม่มี | ตรวจว่า WDCS อัปเดตล่าสุดหรือยัง |
| 🟡 พบ WDCS / ไม่พบ FC | WDCS มี แต่ FC ไม่มี | ตรวจว่า FC Export ครบหรือยัง |
| 🔴 ไม่พบทั้ง 2 ระบบ | ทั้ง FC และ WDCS ไม่มีบันทึก | **ติดตามกับคลังด่วน** |
""")

                st.divider()

                # ── 4 detail tabs (✅✅ first = default) ──────────────────────
                ct1, ct2, ct3, ct4 = st.tabs([
                    f"✅✅ ยืนยันทั้ง 2 ระบบ ({n_both_confirmed})",
                    f"🟡 พบ FC / ไม่พบ WDCS ({len(grp_fc_only)})",
                    f"🟡 พบ WDCS / ไม่พบ FC ({len(grp_wdcs_only)})",
                    f"🔴 ไม่พบทั้ง 2 ระบบ ({len(grp_missing_both)})",
                ])

                with ct1:
                    st.caption("✅ Order ทั้งหมดที่คลังมีบันทึก — FC matched + WDCS matched รวมในตารางเดียว")

                    def _build_fc_unified(df: pd.DataFrame) -> pd.DataFrame:
                        if df.empty:
                            return pd.DataFrame(columns=["ระบบ","SPX Order SN","SPX Tracking","WMS Order Key","Brand","Total Box","Transport / Load No","Carrier"])
                        r = df.reset_index(drop=True)
                        return pd.DataFrame({
                            "ระบบ":                "FC",
                            "SPX Order SN":        r["SPX Order SN"]    if "SPX Order SN"    in r.columns else "",
                            "SPX Tracking":        r["SPX Tracking"]    if "SPX Tracking"    in r.columns else "",
                            "WMS Order Key":       r["Order no"]        if "Order no"        in r.columns else "",
                            "Brand":               r["Brand In Article"]if "Brand In Article"in r.columns else "",
                            "Total Box":           r["Total Box"]       if "Total Box"       in r.columns else "",
                            "Transport / Load No": r["Truck Load No"]   if "Truck Load No"   in r.columns else "",
                            "Carrier":             r["Carrier"]         if "Carrier"         in r.columns else "",
                        })

                    def _build_wdcs_unified(df: pd.DataFrame) -> pd.DataFrame:
                        if df.empty:
                            return pd.DataFrame(columns=["ระบบ","SPX Order SN","SPX Tracking","WMS Order Key","Brand","Total Box","Transport / Load No","Carrier"])
                        r = df.reset_index(drop=True)
                        _car = r["Vehicleregistration"] if "Vehicleregistration" in r.columns else ""
                        return pd.DataFrame({
                            "ระบบ":                "WDCS",
                            "SPX Order SN":        r["Web Order"]       if "Web Order"       in r.columns else "",
                            "SPX Tracking":        r["SPX Tracking"]    if "SPX Tracking"    in r.columns else "",
                            "WMS Order Key":       r["Web Order"]       if "Web Order"       in r.columns else "",
                            "Brand":               r["Brand In Article"]if "Brand In Article"in r.columns else "",
                            "Total Box":           r["TotalBox"]        if "TotalBox"        in r.columns else "",
                            "Transport / Load No": r["Transport_No"]    if "Transport_No"    in r.columns else "",
                            "Carrier":             _car,
                        })

                    _fc_u  = _build_fc_unified(fc_result.matched_df)
                    _wdcs_u = _build_wdcs_unified(wdcs_result.matched_df)
                    _unified_df = pd.concat([_fc_u, _wdcs_u], ignore_index=True)

                    if not _unified_df.empty:
                        st.dataframe(_unified_df, use_container_width=True, hide_index=True)
                        st.caption(f"FC: {len(_fc_u)} rows | WDCS: {len(_wdcs_u)} rows | รวม {len(_unified_df)} rows")
                    else:
                        st.info("ไม่มี Order ที่ Match")

                with ct2:
                    st.caption("🟡 FC มีบันทึก Order นี้ แต่ WDCS ไม่มี — ตรวจสอบว่า WDCS Export ครบหรือยัง")
                    if grp_fc_only:
                        df_fc_only = fc_result.matched_df[
                            fc_result.matched_df.get("SPX Order SN", pd.Series(dtype=str)).isin(grp_fc_only)
                        ].copy() if "SPX Order SN" in fc_result.matched_df.columns else pd.DataFrame()
                        if not df_fc_only.empty:
                            st.dataframe(df_fc_only, use_container_width=True, hide_index=True)
                        else:
                            st.dataframe(
                                pd.DataFrame({"Order SN (SPX)": list(grp_fc_only)}),
                                use_container_width=True, hide_index=True
                            )
                    else:
                        st.success("🎉 ไม่มี — WDCS มีบันทึกครบทุก Order ที่พบใน FC")

                with ct3:
                    st.caption("🟡 WDCS มีบันทึก Order นี้ แต่ FC ไม่มี — ตรวจสอบว่า FC Export ครบหรือยัง")
                    if grp_wdcs_only:
                        df_wdcs_only = wdcs_result.matched_df[
                            wdcs_result.matched_df["Web Order"].isin(grp_wdcs_only)
                        ].copy() if "Web Order" in wdcs_result.matched_df.columns else pd.DataFrame()
                        if not df_wdcs_only.empty:
                            st.dataframe(df_wdcs_only, use_container_width=True, hide_index=True)
                        else:
                            st.dataframe(
                                pd.DataFrame({"Order SN (SPX)": list(grp_wdcs_only)}),
                                use_container_width=True, hide_index=True
                            )
                    else:
                        st.success("🎉 ไม่มี — FC มีบันทึกครบทุก Order ที่พบใน WDCS")

                with ct4:
                    st.caption("🔴 ต้องตรวจสอบเร่งด่วน — SPX รับแต่ไม่พบใน FC และ WDCS เลย")
                    if grp_missing_both:
                        df_mb = fc_result.missing_in_wms_df[fc_result.missing_in_wms_df[spx_col].isin(grp_missing_both)].copy()
                        st.dataframe(df_mb, use_container_width=True, hide_index=True)
                    else:
                        st.success("🎉 ไม่มี — ทุก Order ของ SPX พบในอย่างน้อยหนึ่งระบบ")

        else:
            render_result(tab_obj, slot_type, result, fv)

    # ── Export ────────────────────────────────────────────────────────────────
    st.divider()
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        if fc_result:
            s = fc_result.summary
            fc_result.matched_df.to_excel(writer, sheet_name="FC_Matched", index=False)
            fc_result.missing_in_wms_df.to_excel(writer, sheet_name="FC_Missing", index=False)
            fc_result.extra_in_wms_df.to_excel(writer, sheet_name="FC_Extra", index=False)
            pd.DataFrame({
                "Metric": ["Matched", "Missing WMS", "Extra WMS", "Match Rate", "Filter"],
                "Value": [s["matched"], s["missing_in_wms"], s["extra_in_wms"], f"{s['match_rate']}%", _fc_filter or "(ทั้งไฟล์)"],
            }).to_excel(writer, sheet_name="FC_Summary", index=False)

        if wdcs_result:
            s = wdcs_result.summary
            wdcs_result.matched_df.to_excel(writer, sheet_name="WDCS_Matched", index=False)
            wdcs_result.missing_in_wms_df.to_excel(writer, sheet_name="WDCS_Missing", index=False)
            wdcs_result.extra_in_wms_df.to_excel(writer, sheet_name="WDCS_Extra", index=False)
            pd.DataFrame({
                "Metric": ["Matched", "Missing WMS", "Extra WMS", "Match Rate", "Filter"],
                "Value": [s["matched"], s["missing_in_wms"], s["extra_in_wms"], f"{s['match_rate']}%", _wdcs_filter or "(ทั้งไฟล์)"],
            }).to_excel(writer, sheet_name="WDCS_Summary", index=False)

    buf.seek(0)
    suffix_parts = []
    if _fc_filter:
        suffix_parts.append(f"FC_{_fc_filter}")
    if _wdcs_filter:
        suffix_parts.append(f"WDCS_{_wdcs_filter}")
    suffix = "_".join(suffix_parts) if suffix_parts else "all"

    st.download_button(
        "📥 Download Excel Report",
        buf,
        file_name=f"reconciliation_SPX_{suffix}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


elif not carrier_file and not has_fc and not has_wdcs:
    st.markdown("""
    ### วิธีใช้งาน
    1. **อัปโหลด Carrier File** — SPX `.pdf`
    2. **อัปโหลด WMS** — FC `.xlsx` และ/หรือ WDCS `.txt` *(upload พร้อมกันได้)*
    3. **ระบุ Truck Load / Transport No.** — Scan, Dropdown, หรือ ทั้งไฟล์
    4. กด **Reconcile** → ดูผลแยก FC | WDCS | Combined

    ---
    > - ⚠️ **Missing in WMS** = 3PL รับแต่คลังไม่มีบันทึก
    > - ⚠️ **Extra in WMS** = คลังปล่อยแต่ 3PL ไม่ได้รับ
    > - 🔴 **Combined: Missing ทั้ง 2 ระบบ** = ต้องตรวจสอบเร่งด่วน
    """)