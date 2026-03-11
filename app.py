"""
Dispatch Reconciliation System — v1.2
Goal: ตรวจสอบว่า สิ่งที่ 3PL มารับ ตรงกับสิ่งที่คลังปล่อยออกไป
"""
import io
import streamlit as st
import pandas as pd

from parsers.spx_parser import parse_spx_pdf, validate_spx_df
from parsers.wdcs_parser import parse_wdcs_txt, validate_wdcs_df
from parsers.fc_parser import parse_fc_xlsx, validate_fc_df
from reconciler.engine import reconcile_spx_wdcs, reconcile_spx_fc, find_matching_transports
from ai.gemini_client import (
    is_available as ai_available,
    generate_summary_report, explain_missing,
    detect_anomalies, fuzzy_match_hint,
)

# ── Page Config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Dispatch Reconciliation", page_icon="📦", layout="wide")
st.title("📦 Dispatch Reconciliation System")
st.caption("ตรวจสอบว่า สิ่งที่ 3PL มารับ = สิ่งที่คลังปล่อยออกไป — v1.2")
st.divider()

# ── Helper: parse carrier bytes → keys set ───────────────────────────────────
def get_carrier_keys(carrier_bytes: bytes, carrier_type: str) -> tuple[pd.DataFrame, set]:
    if carrier_type == "SPX":
        df = parse_spx_pdf(carrier_bytes)
        keys = set(df["order_sn"].dropna().str.strip().replace("", pd.NA).dropna())
        return df, keys
    return pd.DataFrame(), set()


def get_wms_df_full(wms_bytes: bytes, wms_type: str) -> pd.DataFrame:
    """Parse WMS file without any filter — for transport analysis."""
    if wms_type == "WDCS":
        return parse_wdcs_txt(wms_bytes, transport_no="")
    else:
        return parse_fc_xlsx(wms_bytes, truck_load_no="")


def get_cached_wms_df(wms_file, wms_type: str) -> pd.DataFrame:
    """Return parsed WMS df, cached in session_state by filename+size."""
    cache_key = f"wms_df_{wms_file.name}_{wms_file.size}"
    if cache_key not in st.session_state:
        wms_file.seek(0)
        st.session_state[cache_key] = get_wms_df_full(wms_file.read(), wms_type)
        wms_file.seek(0)
    return st.session_state[cache_key]


# ── Step 1: File Upload ───────────────────────────────────────────────────────
st.subheader("Step 1 — อัปโหลดไฟล์")
col_carrier, col_wms = st.columns(2)

carrier_type = None
wms_type = None

with col_carrier:
    st.markdown("**🚚 Carrier File** *(Master — ตั้งหลัก)*")
    carrier_file = st.file_uploader("SPX = .pdf | Kerry = .xlsx", type=["pdf", "xlsx", "xls"], key="carrier")
    if carrier_file:
        ext = carrier_file.name.split(".")[-1].lower()
        if ext == "pdf":
            st.success("✅ ตรวจจับ: **SPX** (PDF)")
            carrier_type = "SPX"
        elif ext in ("xlsx", "xls"):
            st.info("ℹ️ ตรวจจับ: **Kerry** (Excel) — Phase 2")
            carrier_type = "Kerry"
        st.session_state["carrier_type"] = carrier_type
    else:
        carrier_type = st.session_state.get("carrier_type")

with col_wms:
    st.markdown("**🏭 WMS Export DO File**")
    wms_file = st.file_uploader("WDCS = .txt | FC = .xlsx", type=["txt", "xlsx", "xls"], key="wms")
    if wms_file:
        ext_wms = wms_file.name.split(".")[-1].lower()
        if ext_wms == "txt":
            st.success("✅ ตรวจจับ: **WDCS** (Tab-delimited TXT)")
            wms_type = "WDCS"
        elif ext_wms in ("xlsx", "xls"):
            st.success("✅ ตรวจจับ: **FC** (Excel)")
            wms_type = "FC"
        st.session_state["wms_type"] = wms_type
    else:
        wms_type = st.session_state.get("wms_type")

# ── Step 2: Transport / Load No. ──────────────────────────────────────────────
filter_value = ""
both_ready = carrier_file and wms_file and carrier_type == "SPX" and wms_type in ("WDCS", "FC")


if both_ready:
    st.divider()
    st.subheader("Step 2 — ระบุ Transport / Load No.")

    filter_col = "Transport_No" if wms_type == "WDCS" else "Truck Load No"
    match_col  = "Web Order"    if wms_type == "WDCS" else "Weborder DO"
    label_name = "Transport No. (WDCS)" if wms_type == "WDCS" else "Truck Load No. (FC)"

    # ── FC: mode selector (Scan / Dropdown / ทั้งไฟล์) ──────────────────────
    if wms_type == "FC":
        input_mode_fc = st.radio(
            "วิธีระบุ Truck Load No.",
            options=["📷 Scan / พิมพ์เอง", "🔍 ค้นหาจาก Dropdown", "📦 ทั้งไฟล์ (ไม่ Filter)"],
            horizontal=True,
            key="input_mode_fc",
            help="Scan = เร็ว | Dropdown = ระบบโหลดรายการ TLD จากไฟล์ | ทั้งไฟล์ = Reconcile ทุก order",
        )

        if input_mode_fc == "📷 Scan / พิมพ์เอง":
            filter_value = st.text_input(
                "Truck Load No. (FC)",
                key="transport_manual_fc",
                placeholder="Scan barcode หรือพิมพ์ เช่น TLD2603001522",
            )
            if filter_value:
                st.caption(f"🔖 ใช้: **{filter_value}**")

        elif input_mode_fc == "🔍 ค้นหาจาก Dropdown":
            fc_tld_cache_key = f"fc_tld_list_{wms_file.name}_{wms_file.size}"
            if fc_tld_cache_key not in st.session_state:
                col_btn, col_note = st.columns([2, 3])
                with col_btn:
                    if st.button("🔍 โหลดรายการ Truck Load No.", type="secondary", key="fc_load_btn"):
                        with st.spinner("กำลังอ่านไฟล์..."):
                            try:
                                from parsers.fc_parser import get_fc_load_numbers
                                wms_file.seek(0)
                                tld_list = get_fc_load_numbers(wms_file.read())
                                wms_file.seek(0)
                                st.session_state[fc_tld_cache_key] = tld_list
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ โหลดไม่สำเร็จ: {e}")
                with col_note:
                    st.caption("⚠️ ไฟล์ใหญ่อาจใช้เวลา 5–15 วินาที")
            if fc_tld_cache_key in st.session_state:
                tld_list = st.session_state[fc_tld_cache_key]
                if tld_list:
                    st.success(f"✅ พบ **{len(tld_list)}** Truck Load No. ในไฟล์")
                    search_tld = st.text_input("🔎 กรองรายการ", key="fc_tld_search", placeholder="พิมพ์บางส่วน เช่น TLD2603")
                    filtered_tld = (
                        [t for t in tld_list if search_tld.strip().lower() in t.lower()]
                        if search_tld.strip() else tld_list
                    )
                    if filtered_tld:
                        selected_tld = st.selectbox("Truck Load No.", options=filtered_tld, key="fc_tld_select", label_visibility="collapsed")
                        filter_value = selected_tld or ""
                        if filter_value:
                            st.caption(f"🔖 เลือก: **{filter_value}**")
                    else:
                        st.warning(f"ไม่พบ TLD ที่มี '{search_tld}'")
                    if st.button("🔄 โหลดใหม่", key="fc_tld_refresh"):
                        del st.session_state[fc_tld_cache_key]
                        st.rerun()
                else:
                    st.warning("⚠️ ไม่พบ Truck Load No. ในไฟล์ — จะ Reconcile ทั้งไฟล์")

        else:  # ทั้งไฟล์
            filter_value = ""
            st.caption("📦 Reconcile ทั้งไฟล์ — จับคู่ด้วย 3PL Tracking No. และ Order no")

        # ── FC Inspect (debug) ────────────────────────────────────────────────
        with st.expander("🔬 ตรวจสอบไฟล์ FC (debug)"):
            if st.button("วิเคราะห์ไฟล์ FC", key="fc_inspect"):
                with st.spinner("กำลังอ่าน..."):
                    wms_file.seek(0)
                    import io as _io, pandas as _pd
                    _raw = _pd.read_excel(_io.BytesIO(wms_file.read()), header=1, dtype=str, nrows=10)
                    wms_file.seek(0)
                    _raw.columns = [str(c).strip() for c in _raw.columns]
                    fc_full = get_cached_wms_df(wms_file, wms_type)

                # ── Raw columns (ALL, not just KEY_COLUMNS) ───────────────────
                st.markdown("**📋 Raw column ทั้งหมด (row 2 header):**")
                st.write(list(_raw.columns))

                # ── Parsed df stats ──────────────────────────────────────────
                st.write(f"**Rows parsed (KEY_COLUMNS only):** {len(fc_full)}")
                if "Weborder DO" in fc_full.columns:
                    _wo = fc_full["Weborder DO"].dropna()
                    st.write(f"**Weborder DO non-null:** {len(_wo)} | Sample: {_wo.head(3).tolist()}")
                else:
                    st.error("❌ ไม่พบ column 'Weborder DO'")
                if "Order no" in fc_full.columns:
                    _on = fc_full["Order no"].dropna().str.strip().replace("", _pd.NA).dropna()
                    st.write(f"**Order no non-null:** {len(_on)} | Sample (first 3): {_on.head(3).tolist()}")
                    # Search for CMGSHP pattern
                    _cmg = _on[_on.str.contains("CMGSHP", na=False)]
                    st.write(f"**Order no ที่มี 'CMGSHP':** {len(_cmg)} rows | Sample: {_cmg.head(3).tolist()}")
                    # Carrier column breakdown
                if "Carrier" in fc_full.columns:
                    _car = fc_full["Carrier"].dropna().str.strip().replace("", _pd.NA).dropna()
                    st.write(f"**Carrier unique:** {_car.unique().tolist()[:10]}")
                if "Document Type Name" in fc_full.columns:
                    _dt = fc_full["Document Type Name"].dropna().str.strip().replace("", _pd.NA).dropna()
                    _dt_counts = _dt.value_counts().head(10)
                    st.write(f"**Document Type Name unique ({len(_dt_counts)} types):**")
                    st.dataframe(_dt_counts.reset_index(), use_container_width=True, hide_index=True)
                if "Group Order Type" in fc_full.columns:
                    _got = fc_full["Group Order Type"].dropna().str.strip().replace("", _pd.NA).dropna()
                    st.write(f"**Group Order Type unique:** {_got.unique().tolist()[:10]}")
                if "3PL Transport Tracking No" in fc_full.columns:
                    _trk = fc_full["3PL Transport Tracking No"].dropna().str.strip().replace("", _pd.NA).dropna()
                    st.write(f"**3PL Tracking non-null:** {len(_trk)} | Sample: {_trk.head(3).tolist()}")
                if "Truck Load No" in fc_full.columns:
                    _tld = fc_full["Truck Load No"].dropna().str.strip().replace("", _pd.NA).dropna()
                    st.write(f"**Truck Load No non-null:** {len(_tld)} | Sample: {_tld.head(3).tolist()}")
    else:
        # ── Mode selector ─────────────────────────────────────────────────────
        input_mode = st.radio(
            "วิธีระบุ Transport / Load No.",
            options=["📷 Scan / พิมพ์เอง", "🔍 ค้นหาจาก Dropdown"],
            horizontal=True,
            key="input_mode",
            help="Scan = เร็ว ไม่ต้อง analyze ไฟล์ | Dropdown = ระบบจะหา Transport ที่ตรงกับ Carrier ให้",
        )

        # ── Mode A: Scan / Type ───────────────────────────────────────────────
        if input_mode == "📷 Scan / พิมพ์เอง":
            filter_value = st.text_input(
                label_name,
                key="transport_manual",
                placeholder="Scan barcode หรือพิมพ์ตัวเลข เช่น 3260001318",
                help="รองรับ Barcode Scanner — Scan แล้วกด Enter",
            )
            if filter_value:
                st.caption(f"🔖 ใช้: **{filter_value}**")

        # ── Mode B: Dropdown with on-demand analysis ──────────────────────────
        else:
            cache_key = f"transport_options_{carrier_file.name}_{wms_file.name}"

            if cache_key not in st.session_state:
                col_btn, col_note = st.columns([2, 3])
                with col_btn:
                    if st.button("🔍 วิเคราะห์และโหลดรายการ", type="secondary", key="analyze_btn"):
                        with st.spinner("กำลังวิเคราะห์ไฟล์ — อาจใช้เวลาสักครู่..."):
                            try:
                                carrier_file.seek(0)
                                _, carrier_keys = get_carrier_keys(carrier_file.read(), carrier_type)
                                carrier_file.seek(0)
                                wms_full_df = get_cached_wms_df(wms_file, wms_type)
                                matches = find_matching_transports(carrier_keys, wms_full_df, filter_col, match_col)
                                st.session_state[cache_key] = matches
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ วิเคราะห์ไฟล์ไม่สำเร็จ: {e}")
                with col_note:
                    st.caption("⚠️ ไฟล์ใหญ่อาจใช้เวลา 5–15 วินาที")

            if cache_key in st.session_state:
                matches = st.session_state[cache_key]

                def make_label(m: dict) -> str:
                    icon = "✅" if m["match_count"] > 0 else "  "
                    return f"{m['transport_no']}  {icon} {m['match_count']} match / {m['total']} total"

                all_labels = [make_label(m) for m in matches]
                all_values = [m["transport_no"] for m in matches]
                matched_labels = [l for l, m in zip(all_labels, matches) if m["match_count"] > 0]
                matched_values = [v for v, m in zip(all_values, matches) if m["match_count"] > 0]
                unmatched_labels = [l for l, m in zip(all_labels, matches) if m["match_count"] == 0]
                unmatched_values = [v for v, m in zip(all_values, matches) if m["match_count"] == 0]

                n_with_match = len(matched_values)
                if n_with_match:
                    st.success(f"✅ พบ **{n_with_match}** Transport/Load ที่ตรงกับ Carrier (จาก {len(all_values)} ทั้งหมด)")
                else:
                    st.warning("⚠️ ไม่พบ Transport/Load ที่ตรงกับ Carrier — ตรวจสอบช่วงวันที่ของไฟล์")

                search_text = st.text_input(
                    "🔎 กรองรายการ (wildcard)",
                    key="transport_search",
                    placeholder="พิมพ์บางส่วน เช่น 3260",
                )

                if search_text.strip():
                    s = search_text.strip().lower()
                    filtered_labels = [l for l, v in zip(all_labels, all_values) if s in v.lower()]
                    filtered_values = [v for v in all_values if s in v.lower()]
                else:
                    filtered_labels = matched_labels + unmatched_labels
                    filtered_values = matched_values + unmatched_values

                if filtered_labels:
                    default_idx = 0
                    if search_text.strip() and search_text.strip() in filtered_values:
                        default_idx = filtered_values.index(search_text.strip())
                    selected_label = st.selectbox(
                        label_name, options=filtered_labels,
                        index=default_idx, key="transport_select",
                        label_visibility="collapsed",
                    )
                    filter_value = selected_label.split(" ")[0] if selected_label else ""
                else:
                    st.warning(f"ไม่พบ '{search_text}'")

                if filter_value:
                    match_info = next((m for m in matches if m["transport_no"] == filter_value), None)
                    if match_info and match_info["match_count"] > 0:
                        st.info(f"🔖 เลือก: **{filter_value}** — {match_info['match_count']} match / {match_info['total']} orders")
                    else:
                        st.caption(f"🔖 เลือก: **{filter_value}**")

                if st.button("🔄 โหลดใหม่", key="clear_cache", help="วิเคราะห์ไฟล์ใหม่อีกครั้ง"):
                    del st.session_state[cache_key]
                    st.rerun()

elif carrier_file and wms_file and not both_ready:
    st.divider()
    st.warning("⚠️ Phase นี้รองรับ **SPX + WDCS** และ **SPX + FC** — Kerry จะพร้อมใน Phase 2")

# ── Step 3: Reconcile ─────────────────────────────────────────────────────────
st.divider()
run_btn = st.button(
    "🔍 เริ่ม Reconcile",
    type="primary",
    disabled=not both_ready,
    use_container_width=True,
)

# ── Main Reconcile Logic ──────────────────────────────────────────────────────
if run_btn and both_ready:
    _prog = st.progress(0, text="📄 กำลังอ่าน Carrier file...")

    carrier_file.seek(0)
    try:
        spx_df = parse_spx_pdf(carrier_file.read())
        ok, err = validate_spx_df(spx_df)
        if not ok:
            _prog.empty()
            st.error(f"❌ SPX Parser Error: {err}")
            st.stop()
    except Exception as e:
        _prog.empty()
        st.error(f"❌ ไม่สามารถอ่าน SPX PDF: {e}")
        st.stop()

    _prog.progress(30, text=f"✅ อ่าน SPX เสร็จ ({len(spx_df)} orders) — กำลังอ่าน WMS...")

    try:
        if wms_type == "WDCS":
            wms_file.seek(0)
            wdcs_df = parse_wdcs_txt(wms_file.read(), transport_no=filter_value)
            wms_file.seek(0)
            ok2, err2 = validate_wdcs_df(wdcs_df, filter_value)
            if not ok2:
                _prog.empty()
                st.error(f"❌ WDCS: {err2}")
                st.stop()
            _prog.progress(70, text=f"✅ อ่าน WDCS เสร็จ ({len(wdcs_df)} rows) — กำลัง Reconcile...")
            result = reconcile_spx_wdcs(spx_df, wdcs_df, transport_no=filter_value)
        else:
            _prog.progress(40, text="📊 กำลังอ่าน FC Excel (อาจใช้เวลา 10–30 วินาที)...")
            fc_df_full = get_cached_wms_df(wms_file, wms_type)
            if filter_value and filter_value.strip() and "Truck Load No" in fc_df_full.columns:
                # Filter by specific Load No
                fc_df = fc_df_full[fc_df_full["Truck Load No"].astype(str).str.strip() == filter_value.strip()].copy()
            else:
                # No TLD filter — use full file (match by tracking / order key)
                fc_df = fc_df_full.copy()
            ok2, err2 = validate_fc_df(fc_df, filter_value)
            if not ok2:
                _prog.empty()
                st.error(f"❌ FC: {err2}")
                st.stop()
            _prog.progress(70, text=f"✅ อ่าน FC เสร็จ ({len(fc_df)} rows) — กำลัง Reconcile...")
            result = reconcile_spx_fc(spx_df, fc_df, truck_load_no=filter_value)
    except Exception as e:
        _prog.empty()
        st.error(f"❌ ไม่สามารถ Reconcile: {e}")
        st.stop()

    _prog.progress(100, text="✅ Reconcile เสร็จสิ้น!")
    _prog.empty()
    st.success("✅ Reconcile สำเร็จ!")
    if filter_value:
        st.info(f"🔖 **{result.filter_key}** = `{result.filter_value}`")
    else:
        st.warning("⚠️ ไม่ได้ระบุ Transport / Load No. — ใช้ข้อมูล WMS ทั้งหมด")

    st.divider()

    # ── Summary Cards ─────────────────────────────────────────────────────────
    st.subheader("📊 สรุปผล")
    s = result.summary
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("🚚 Carrier", s["carrier_total"], help="Orders ที่ 3PL มารับ")
    c2.metric("🏭 WMS (filtered)", s["wms_total"])
    c3.metric("✅ Matched", s["matched"])
    c4.metric("⚠️ Missing in WMS", s["missing_in_wms"], delta_color="inverse")
    c5.metric("⚠️ Extra in WMS", s["extra_in_wms"], delta_color="inverse")

    pct = s["match_rate"]
    color = "green" if pct >= 95 else "orange" if pct >= 80 else "red"
    st.markdown(f"**Match Rate:** :{color}[**{pct}%**]")

    # FC: show match method breakdown
    if wms_type == "FC" and "matched_by_tracking" in s:
        if s.get("fc_has_tracking"):
            st.caption(f"Match method — Tracking: {s['matched_by_tracking']} | Weborder DO: {s['matched_by_orderkey']}")
        else:
            st.caption("ℹ️ FC ไม่มี 3PL Tracking No. — จับคู่ด้วย Weborder DO อย่างเดียว")

    # FC: Load No breakdown table (when no specific filter)
    if wms_type == "FC" and not filter_value and not result.matched_df.empty:
        if "Truck Load No" in result.matched_df.columns:
            _load_df = result.matched_df.copy()
            _load_df["Truck Load No"] = _load_df["Truck Load No"].astype(str).str.strip()
            _load_df = _load_df[_load_df["Truck Load No"].replace("nan", "").ne("")]
            if not _load_df.empty:
                st.divider()
                st.subheader("📦 สรุปตาม Truck Load No.")
                _agg: dict = {"Weborder DO": "count"}
                if "Total Box" in _load_df.columns:
                    _load_df["Total Box"] = pd.to_numeric(_load_df["Total Box"], errors="coerce")
                    _agg["Total Box"] = "sum"
                _summary_tbl = _load_df.groupby("Truck Load No", sort=False).agg(_agg).reset_index()
                _summary_tbl.columns = (
                    ["Truck Load No", "Matched Orders", "Total Box"]
                    if "Total Box" in _agg
                    else ["Truck Load No", "Matched Orders"]
                )
                _summary_tbl = _summary_tbl.sort_values("Truck Load No", ascending=False, key=lambda s: s.astype(str))
                st.dataframe(_summary_tbl, use_container_width=True, hide_index=True)

    st.divider()

    # ── Detail Tabs ───────────────────────────────────────────────────────────
    st.subheader("📋 รายละเอียด")
    t1, t2, t3 = st.tabs([
        f"✅ Matched ({s['matched']})",
        f"⚠️ Missing in WMS ({s['missing_in_wms']})",
        f"⚠️ Extra in WMS ({s['extra_in_wms']})",
    ])
    with t1:
        st.dataframe(result.matched_df, use_container_width=True, hide_index=True) if not result.matched_df.empty else st.info("ไม่มีรายการที่ Match")
    with t2:
        st.caption("3PL รับไปแล้ว แต่ WMS ไม่มีบันทึกใน Transport/Load นี้")
        st.dataframe(result.missing_in_wms_df, use_container_width=True, hide_index=True) if not result.missing_in_wms_df.empty else st.success("🎉 ครบถ้วน")
    with t3:
        st.caption("WMS ปล่อยออกไปแล้ว แต่ 3PL ไม่ได้ Scan/รับ")
        st.dataframe(result.extra_in_wms_df, use_container_width=True, hide_index=True) if not result.extra_in_wms_df.empty else st.success("🎉 ครบถ้วน")

    # ── Export ────────────────────────────────────────────────────────────────
    st.divider()
    buf = io.BytesIO()
    suffix = f"_{filter_value}" if filter_value else ""
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        result.matched_df.to_excel(writer, sheet_name="Matched", index=False)
        result.missing_in_wms_df.to_excel(writer, sheet_name="Missing_in_WMS", index=False)
        result.extra_in_wms_df.to_excel(writer, sheet_name="Extra_in_WMS", index=False)
        pd.DataFrame({
            "Metric": ["Carrier Total", "WMS Total", "Matched", "Missing in WMS",
                       "Extra in WMS", "Match Rate (%)", "Carrier", "WMS", "Filter", "Filter Value"],
            "Value": [s["carrier_total"], s["wms_total"], s["matched"], s["missing_in_wms"],
                      s["extra_in_wms"], s["match_rate"], result.carrier_type, result.wms_type,
                      result.filter_key, result.filter_value],
        }).to_excel(writer, sheet_name="Summary", index=False)
    buf.seek(0)
    st.download_button(
        "📥 Download Excel Report", buf,
        file_name=f"reconciliation_{result.carrier_type}_{result.wms_type}{suffix}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    # ── AI Analysis ───────────────────────────────────────────────────────────
    if ai_available():
        st.divider()
        st.subheader("🤖 AI Analysis (Gemini Flash)")
        ai_tab1, ai_tab2, ai_tab3, ai_tab4 = st.tabs([
            "📋 Summary Report", "❓ Explain Missing", "🔍 Anomaly Detect", "🔗 Fuzzy Match"
        ])

        with ai_tab1:
            if st.button("สรุปผลภาษาไทย", key="ai_summary"):
                with st.spinner("Gemini กำลังวิเคราะห์..."):
                    out = generate_summary_report(s, result.carrier_type, result.wms_type, filter_value)
                st.markdown(out)

        with ai_tab2:
            if st.button("อธิบาย Missing Orders", key="ai_explain", disabled=result.missing_in_wms_df.empty):
                with st.spinner("Gemini กำลังวิเคราะห์..."):
                    out = explain_missing(result.missing_in_wms_df, result.carrier_type, result.wms_type)
                st.markdown(out)
            if result.missing_in_wms_df.empty:
                st.success("🎉 ไม่มี Missing orders")

        with ai_tab3:
            if st.button("ตรวจหา Anomaly", key="ai_anomaly"):
                with st.spinner("Gemini กำลังวิเคราะห์..."):
                    out = detect_anomalies(result.missing_in_wms_df, result.extra_in_wms_df, result.matched_df)
                st.markdown(out)

        with ai_tab4:
            st.caption("จับคู่ orders ที่ format อาจต่างกันเล็กน้อย")
            if st.button("Fuzzy Match", key="ai_fuzzy",
                         disabled=result.missing_in_wms_df.empty or result.extra_in_wms_df.empty):
                with st.spinner("Gemini กำลังวิเคราะห์..."):
                    out = fuzzy_match_hint(result.missing_in_wms_df, result.extra_in_wms_df)
                st.markdown(out)
            if result.missing_in_wms_df.empty or result.extra_in_wms_df.empty:
                st.info("ต้องมีทั้ง Missing และ Extra orders จึงจะ Fuzzy Match ได้")

elif not carrier_file and not wms_file:
    st.markdown("""
    ### วิธีใช้งาน
    1. **อัปโหลด Carrier File** — SPX `.pdf`
    2. **อัปโหลด WMS Export DO** — WDCS `.txt` หรือ FC `.xlsx`
    3. ระบบ **วิเคราะห์อัตโนมัติ** — แสดง Transport/Load ที่ตรงกับ Carrier (✅)
    4. **Scan barcode** หรือพิมพ์ค้นหาแบบ wildcard เพื่อเลือก Transport No.
    5. กด **Reconcile** → Download Excel

    ---
    > - ⚠️ **Missing in WMS** = 3PL รับแต่คลังไม่มีบันทึก
    > - ⚠️ **Extra in WMS** = คลังปล่อยแต่ 3PL ไม่ได้รับ
    """)