"""
Dispatch Reconciliation System — v2.0
รองรับ: SPX (หลาย PDF) + TLD Report | FC Export DO | WDCS (รวมกันได้)
2 มุมมอง: SPX เป็นหลัก (ขาดอะไรใน WMS) / WMS เป็นหลัก (ขาดอะไรใน SPX)
"""
import io
import streamlit as st
import pandas as pd

from parsers.spx_parser   import parse_spx_pdf,  validate_spx_df
from parsers.wdcs_parser  import parse_wdcs_txt,  validate_wdcs_df
from parsers.fc_parser    import parse_fc_xlsx,   validate_fc_df,  get_fc_load_numbers
from parsers.tld_parser   import parse_tld_xls,   validate_tld_df
from reconciler.engine    import (
    reconcile_spx_wdcs, reconcile_spx_fc,
    reconcile_spx_tld,  find_matching_transports,
)

# ── Page Config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Dispatch Reconciliation", page_icon="📦", layout="wide")
st.title("📦 Dispatch Reconciliation System")
st.caption("v2.0 — SPX หลายไฟล์ | TLD Report | FC Export DO | WDCS | 2 มุมมอง")
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

def get_cached_fc(fc_file) -> pd.DataFrame:
    return _get_cached(fc_file, "fc_full", lambda b: parse_fc_xlsx(b, truck_load_no=""))

def get_cached_wdcs(wdcs_file) -> pd.DataFrame:
    return _get_cached(wdcs_file, "wdcs_full", lambda b: parse_wdcs_txt(b, transport_no=""))


# ══════════════════════════════════════════════════════════════════════════════
# Step 1 — Upload
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("Step 1 — อัปโหลดไฟล์")

# ── SPX: multi-file ───────────────────────────────────────────────────────────
st.markdown("**🚚 Carrier: SPX** *(เลือกได้หลายไฟล์ในคราวเดียว)*")
carrier_files = st.file_uploader(
    "SPX = .pdf  (Ctrl+click หรือ Shift+click เพื่อเลือกหลายไฟล์)",
    type=["pdf"],
    accept_multiple_files=True,
    key="carrier",
)
if carrier_files:
    st.success(f"✅ SPX: {len(carrier_files)} ไฟล์  ({', '.join(f.name for f in carrier_files)})")

st.markdown("---")

# ── WMS: TLD (primary) | FC Export DO (optional) | WDCS (optional) ────────────
st.markdown("**🏭 WMS — เลือกอย่างน้อย 1 ระบบ**")
col_tld, col_fc, col_wdcs = st.columns(3)

with col_tld:
    st.markdown("📋 **TLD Report** *(FC หลัก)*")
    tld_files = st.file_uploader(
        "TLD_Report .xls (เลือกได้หลายไฟล์)",
        type=["xls"],
        accept_multiple_files=True,
        key="tld_file",
    )
    if tld_files:
        st.success(f"✅ TLD: {len(tld_files)} ไฟล์")

with col_fc:
    st.markdown("📊 **FC Export DO** *(เสริม)*")
    fc_file = st.file_uploader("FC = .xlsx", type=["xlsx", "xls"], key="fc_file")
    if fc_file:
        st.success("✅ FC Export DO")

with col_wdcs:
    st.markdown("📄 **WDCS Export DO**")
    wdcs_file = st.file_uploader("WDCS = .txt", type=["txt"], key="wdcs_file")
    if wdcs_file:
        st.success("✅ WDCS")

has_spx   = len(carrier_files) > 0
has_tld   = len(tld_files) > 0
has_fc    = fc_file is not None
has_wdcs  = wdcs_file is not None
any_wms   = has_tld or has_fc or has_wdcs
both_ready = has_spx and any_wms


# ══════════════════════════════════════════════════════════════════════════════
# Step 2 — Filters
# ══════════════════════════════════════════════════════════════════════════════
fc_filter   = ""
wdcs_filter = ""

if both_ready:
    st.divider()
    st.subheader("Step 2 — ระบุ Transport / Load No.")

    n_wms_cols = sum([has_tld, has_fc, has_wdcs])
    filter_cols = st.columns(n_wms_cols) if n_wms_cols > 1 else [st.container()]
    col_idx = 0

    # ── TLD: auto-detect TLD numbers ─────────────────────────────────────────
    if has_tld:
        with filter_cols[col_idx]:
            col_idx += 1
            st.markdown("**📋 TLD Report — TLD No.**")
            _tld_nos = []
            for tf in tld_files:
                _cache_key = f"tld_no_{tf.name}_{tf.size}"
                if _cache_key not in st.session_state:
                    tf.seek(0)
                    _, _tno = parse_tld_xls(tf.read())
                    tf.seek(0)
                    st.session_state[_cache_key] = _tno
                _tno = st.session_state[_cache_key]
                _tld_nos.append(_tno or tf.name)
            st.info(f"🔖 TLD ที่จะ Reconcile: **{', '.join(_tld_nos)}**")
            st.caption("TLD number อ่านจากหัวไฟล์อัตโนมัติ — ไม่ต้องระบุเพิ่ม")

    # ── FC Export DO Filter ───────────────────────────────────────────────────
    if has_fc:
        with filter_cols[col_idx]:
            col_idx += 1
            st.markdown("**📊 FC Export DO — Truck Load No.**")
            input_mode_fc = st.radio(
                "วิธีระบุ (FC)",
                options=["📷 Scan / พิมพ์เอง", "🔍 Dropdown", "📦 ทั้งไฟล์"],
                horizontal=True,
                key="input_mode_fc",
            )
            if input_mode_fc == "📷 Scan / พิมพ์เอง":
                fc_filter = st.text_input("Truck Load No. (FC)", key="fc_manual", placeholder="เช่น TLD2603001522")
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
            else:
                fc_filter = ""
                st.caption("📦 Reconcile ทั้งไฟล์ FC")

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

    # ── WDCS Filter ───────────────────────────────────────────────────────────
    if has_wdcs:
        with filter_cols[col_idx]:
            col_idx += 1
            st.markdown("**📄 WDCS — Transport No.**")
            input_mode_wdcs = st.radio(
                "วิธีระบุ (WDCS)",
                options=["📷 Scan / พิมพ์เอง", "🔍 Dropdown"],
                horizontal=True,
                key="input_mode_wdcs",
            )
            if input_mode_wdcs == "📷 Scan / พิมพ์เอง":
                wdcs_filter = st.text_input("Transport No. (WDCS)", key="wdcs_manual", placeholder="เช่น 3260001318")
                if wdcs_filter:
                    st.caption(f"🔖 WDCS: **{wdcs_filter}**")
            else:
                wdcs_cache_key = f"wdcs_transport_{wdcs_file.name}"
                if wdcs_cache_key not in st.session_state:
                    if st.button("🔍 วิเคราะห์ WDCS", key="wdcs_analyze_btn"):
                        with st.spinner("กำลังวิเคราะห์..."):
                            try:
                                # Parse SPX (first file only for quick scan)
                                carrier_files[0].seek(0)
                                spx_tmp = parse_spx_pdf(carrier_files[0].read())
                                carrier_files[0].seek(0)
                                carrier_keys = set(spx_tmp["order_sn"].dropna().str.strip().replace("", pd.NA).dropna())
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

elif carrier_files and not any_wms:
    st.info("⬆️ กรุณา upload TLD Report (.xls) และ/หรือ FC Export DO (.xlsx) / WDCS (.txt)")


# ══════════════════════════════════════════════════════════════════════════════
# Step 3 — Perspective + Reconcile Button
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
perspective_col, btn_col = st.columns([2, 1])

with perspective_col:
    st.markdown("**👁️ มุมมองการตรวจสอบ**")
    perspective = st.radio(
        "มุมมอง",
        options=[
            "🚚 SPX เป็นหลัก — ตรวจว่าขาดอะไรใน WMS",
            "📦 WMS เป็นหลัก — ตรวจว่าอะไรใน WMS ที่ไม่มีใน SPX",
        ],
        horizontal=True,
        key="perspective",
        label_visibility="collapsed",
    )

with btn_col:
    run_btn = st.button(
        "🔍 เริ่ม Reconcile",
        type="primary",
        disabled=not both_ready,
        use_container_width=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Reconcile Logic
# ══════════════════════════════════════════════════════════════════════════════
if run_btn and both_ready:
    _prog = st.progress(0, text="📄 กำลังอ่าน SPX...")

    # ── Parse & concat multi-file SPX ─────────────────────────────────────────
    spx_frames = []
    for i, cf in enumerate(carrier_files):
        try:
            cf.seek(0)
            _df = parse_spx_pdf(cf.read())
            cf.seek(0)
            ok, err = validate_spx_df(_df)
            if ok:
                _df["_source_file"] = cf.name
                spx_frames.append(_df)
            else:
                st.warning(f"⚠️ {cf.name}: {err}")
        except Exception as e:
            st.warning(f"⚠️ ไม่สามารถอ่าน {cf.name}: {e}")

    if not spx_frames:
        _prog.empty(); st.error("❌ ไม่สามารถอ่าน SPX ได้เลย"); st.stop()

    spx_df = pd.concat(spx_frames, ignore_index=True).drop_duplicates(subset=["order_sn"])
    _prog.progress(20, text=f"✅ SPX: {len(spx_df)} orders ({len(carrier_files)} ไฟล์)")

    _tld_result  = None
    _fc_result   = None
    _wdcs_result = None
    errors = []

    # ── Reconcile TLD (concat all TLD files) ──────────────────────────────────
    if has_tld:
        _prog.progress(30, text="📋 กำลังอ่าน TLD Report...")
        tld_frames = []
        tld_nos    = []
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

        if tld_frames:
            tld_df_all = pd.concat(tld_frames, ignore_index=True)
            tld_no_str = ", ".join(t for t in tld_nos if t)
            _prog.progress(50, text=f"✅ TLD: {len(tld_df_all)} rows — Reconcile...")
            try:
                _tld_result = reconcile_spx_tld(spx_df, tld_df_all, tld_no=tld_no_str)
            except Exception as e:
                errors.append(f"TLD Reconcile Error: {e}")

    # ── Reconcile FC Export DO ─────────────────────────────────────────────────
    if has_fc:
        _prog.progress(55, text="📊 กำลังอ่าน FC Export DO...")
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
                _prog.progress(70, text=f"✅ FC: {len(fc_df)} rows — Reconcile...")
                _fc_result = reconcile_spx_fc(spx_df, fc_df, truck_load_no=fc_filter)
        except Exception as e:
            errors.append(f"FC Error: {e}")

    # ── Reconcile WDCS ────────────────────────────────────────────────────────
    if has_wdcs:
        _prog.progress(75, text="📄 กำลังอ่าน WDCS...")
        try:
            wdcs_file.seek(0)
            wdcs_df = parse_wdcs_txt(wdcs_file.read(), transport_no=wdcs_filter)
            wdcs_file.seek(0)
            ok3, err3 = validate_wdcs_df(wdcs_df, wdcs_filter)
            if not ok3:
                errors.append(f"WDCS: {err3}")
            else:
                _prog.progress(90, text=f"✅ WDCS: {len(wdcs_df)} rows — Reconcile...")
                _wdcs_result = reconcile_spx_wdcs(spx_df, wdcs_df, transport_no=wdcs_filter)
        except Exception as e:
            errors.append(f"WDCS Error: {e}")

    _prog.progress(100, text="✅ เสร็จสิ้น")
    _prog.empty()

    for e in errors:
        st.error(f"❌ {e}")

    if _tld_result or _fc_result or _wdcs_result:
        st.session_state["recon_results"] = {
            "tld_result":  _tld_result,
            "fc_result":   _fc_result,
            "wdcs_result": _wdcs_result,
            "fc_filter":   fc_filter,
            "wdcs_filter": wdcs_filter,
            "spx_count":   len(spx_df),
            "spx_files":   [f.name for f in carrier_files],
        }
    else:
        st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Display Results
# ══════════════════════════════════════════════════════════════════════════════
if "recon_results" in st.session_state:
    _rs         = st.session_state["recon_results"]
    tld_result  = _rs["tld_result"]
    fc_result   = _rs["fc_result"]
    wdcs_result = _rs["wdcs_result"]
    _fc_filter  = _rs["fc_filter"]
    _wdcs_filter = _rs["wdcs_filter"]

    # Current perspective
    _persp = st.session_state.get("perspective", "")
    _wms_master = "WMS เป็นหลัก" in _persp

    st.success("✅ Reconcile สำเร็จ!")
    if _wms_master:
        st.info("📦 มุมมอง WMS เป็นหลัก — (ดูว่า Load ที่ปล่อย ตรงหรือไม่)")
    else:
        st.info("🚚 มุมมอง SPX เป็นหลัก — (ดูว่า SPX มีอะไรบ้าง)")
    st.divider()

    # ── Compute Combined sets ─────────────────────────────────────────────────
    spx_col  = "Order SN (SPX)"
    results_list = [(r, label) for r, label in [
        (tld_result, "TLD"), (fc_result, "FC"), (wdcs_result, "WDCS")
    ] if r is not None]
    has_multi = len(results_list) > 1

    if has_multi:
        # Collect matched SNs per system
        def _get_matched_sns(r, label):
            if label in ("TLD", "FC"):
                col = "SPX Order SN"
                if not r.matched_df.empty and col in r.matched_df.columns:
                    return set(r.matched_df[col].dropna())
            else:  # WDCS
                col = "Web Order"
                if not r.matched_df.empty and col in r.matched_df.columns:
                    return set(r.matched_df[col].dropna())
            return set()

        def _get_miss_sns(r):
            if not r.missing_in_wms_df.empty and spx_col in r.missing_in_wms_df.columns:
                return set(r.missing_in_wms_df[spx_col])
            return set()

        all_matched_sets = {label: _get_matched_sns(r, label) for r, label in results_list}
        all_miss_sets    = {label: _get_miss_sns(r) for r, label in results_list}

        # Union of all matched (= confirmed by at least one WMS)
        all_matched_union = set().union(*all_matched_sets.values())
        # Union of all miss
        all_miss_union = set().union(*all_miss_sets.values())
        # Missing from ALL systems
        grp_missing_both = all_miss_sets[results_list[0][1]]
        for _, label in results_list[1:]:
            grp_missing_both &= all_miss_sets[label]

        n_both_confirmed = sum(
            len(r.matched_df) for r, _ in results_list if not r.matched_df.empty
        )

        # per-pair groups (for 2-system case)
        if len(results_list) == 2:
            (r1, l1), (r2, l2) = results_list
            s1 = all_matched_sets[l1]
            s2 = all_matched_sets[l2]
            grp_only_1 = s1 - s2
            grp_only_2 = s2 - s1
        else:
            grp_only_1 = grp_only_2 = set()

    # ── Build tab list ────────────────────────────────────────────────────────
    tab_labels = []
    tab_slots  = []  # ("combined"|"TLD"|"FC"|"WDCS", result, filter_str)

    if has_multi:
        tab_labels.append("🔀 Combined")
        tab_slots.append(("combined", None, ""))

    for r, label in results_list:
        s = r.summary
        icon = {"TLD": "📋", "FC": "📊", "WDCS": "📄"}[label]
        if _wms_master:
            tab_labels.append(f"{icon} {label}  ⚠️ Extra {s['extra_in_wms']}  ✅ {s['matched']}")
        else:
            tab_labels.append(f"{icon} {label}  ✅ {s['matched']}  ⚠️ Missing {s['missing_in_wms']}")
        tab_slots.append((label, r, _fc_filter if label == "FC" else _wdcs_filter if label == "WDCS" else r.filter_value))

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
            c4.metric("⚠️ Missing WMS", s["missing_in_wms"],
                      delta=f"-{s['missing_in_wms']}" if s["missing_in_wms"] else None,
                      delta_color="inverse")
            c5.metric("⚠️ Extra WMS", s["extra_in_wms"],
                      delta=f"+{s['extra_in_wms']}" if s["extra_in_wms"] else None,
                      delta_color="inverse")

            pct = s["match_rate"]
            color = "green" if pct >= 95 else "orange" if pct >= 80 else "red"
            st.markdown(f"**Match Rate:** :{color}[**{pct}%**]")

            if label in ("TLD", "FC") and "matched_by_tracking" in s:
                st.caption(f"Match method — Tracking: {s['matched_by_tracking']} | Order SN: {s['matched_by_orderkey']}")

            st.divider()

            # ── Sub-tabs: order depends on perspective ────────────────────────
            if _wms_master:
                sub_labels = [
                    f"⚠️ เช็ค box ขาด/เกิน ({s['extra_in_wms']})",
                    f"✅ Matched ({s['matched']})",
                    f"⚠️ อื่นๆ ที่มีในไฟล์ขนส่ง ({s['missing_in_wms']})",
                ]
            else:
                sub_labels = [
                    f"✅ Matched ({s['matched']})",
                    f"⚠️ อื่นๆ ที่มีในไฟล์ขนส่ง ({s['missing_in_wms']})",
                    f"⚠️ เช็ค box ขาด/เกิน ({s['extra_in_wms']})",
                ]

            dt_tabs = st.tabs(sub_labels)

            # Map tab index to content
            if _wms_master:
                _tab_extra, _tab_match, _tab_miss = dt_tabs
            else:
                _tab_match, _tab_miss, _tab_extra = dt_tabs

            with _tab_match:
                if not result.matched_df.empty:
                    st.dataframe(result.matched_df, use_container_width=True, hide_index=True)
                else:
                    st.info("ไม่มีรายการ Match")

            with _tab_miss:
                st.caption("3PL รับไปแต่ WMS ไม่มีบันทึก")
                if not result.missing_in_wms_df.empty:
                    st.dataframe(result.missing_in_wms_df, use_container_width=True, hide_index=True)
                else:
                    st.success("🎉 ครบถ้วน — ไม่มี Missing")

            with _tab_extra:
                st.caption("WMS ปล่อยออกแต่ 3PL ไม่ได้รับ")
                if not result.extra_in_wms_df.empty:
                    st.dataframe(result.extra_in_wms_df, use_container_width=True, hide_index=True)
                else:
                    st.success("🎉 ครบถ้วน ")

    # ── Render tabs ───────────────────────────────────────────────────────────
    for tab_obj, (slot_type, result, fv) in zip(tabs, tab_slots):
        if slot_type == "combined":
            with tab_obj:
                st.markdown("### 🔀 ภาพรวมจากทุก WMS")
                sys_names = " + ".join(l for _, l in results_list)
                st.caption(f"รวมผล: {sys_names}")

                # Metrics
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("✅ ยืนยัน (รวม)", n_both_confirmed, help="matched rows จากทุก WMS รวมกัน")

                if len(results_list) == 2:
                    (_, l1), (_, l2) = results_list
                    m2.metric(f"🟡 พบ {l1} / ไม่พบ {l2}", len(grp_only_1), delta_color="off")
                    m3.metric(f"🟡 พบ {l2} / ไม่พบ {l1}", len(grp_only_2), delta_color="off")
                else:
                    m2.metric("🟡 พบบาง WMS", len(all_matched_union - (set() if not grp_missing_both else grp_missing_both)), delta_color="off")
                    m3.metric("📊 WMS ทั้งหมด", len(results_list), delta_color="off")

                m4.metric("🔴 ไม่พบทั้งหมด", len(grp_missing_both), delta_color="inverse")

                st.divider()

                # Summary table
                st.markdown("""
| สถานะ | ความหมาย | ต้องทำอะไร |
|---|---|---|
| ✅ ยืนยัน | WMS มีบันทึก | ไม่ต้องทำอะไร |
| 🟡 พบบาง WMS | บาง WMS มีบันทึก บางอันไม่มี | ตรวจสอบ WMS ที่ขาด |
| 🔴 ไม่พบทั้งหมด | ทุก WMS ไม่มีบันทึก | **ตรวจสอบว่าข้อมูลถูกต้องหรือไม่** |
""")
                st.divider()

                # Detail tabs
                if len(results_list) == 2:
                    (_, l1), (_, l2) = results_list
                    ct_labels = [
                        f"✅ ยืนยัน ({n_both_confirmed})",
                        f"🟡 พบ {l1} / ไม่พบ {l2} ({len(grp_only_1)})",
                        f"🟡 พบ {l2} / ไม่พบ {l1} ({len(grp_only_2)})",
                        f"🔴 ไม่พบทั้งหมด ({len(grp_missing_both)})",
                    ]
                else:
                    ct_labels = [
                        f"✅ ยืนยัน ({n_both_confirmed})",
                        f"🔴 ไม่พบทั้งหมด ({len(grp_missing_both)})",
                    ]

                ct_tabs = st.tabs(ct_labels)

                # ── ct1: Unified confirmed table ──────────────────────────────
                _UNIFIED_COLS = ["ระบบ","TLD / Transport No.","SPX Order SN","SPX Tracking",
                                 "Create Date (WMS)","SPX Pickup Time","ระยะเวลา (ชม.)",
                                 "WMS Order Key","Brand","Pallet No","Carrier"]

                def _build_fc_unified(df: pd.DataFrame, label: str, tld_no_val: str = "") -> pd.DataFrame:
                    if df.empty:
                        return pd.DataFrame(columns=_UNIFIED_COLS)
                    r = df.reset_index(drop=True)
                    order_key_col = "Order no" if "Order no" in r.columns else ("Order No" if "Order No" in r.columns else "")
                    load_col      = "Truck Load No"    if "Truck Load No"    in r.columns else ""
                    create_col    = "Create date&time" if "Create date&time" in r.columns else ""
                    dur_col       = "ระยะเวลา SPX-WMS (ชม.)" if "ระยะเวลา SPX-WMS (ชม.)" in r.columns else ""
                    return pd.DataFrame({
                        "ระบบ":               label,
                        "TLD / Transport No.": tld_no_val,
                        "SPX Order SN":        r["SPX Order SN"]              if "SPX Order SN"      in r.columns else "",
                        "SPX Tracking":        r["SPX Tracking"]              if "SPX Tracking"      in r.columns else "",
                        "Create Date (WMS)":   r[create_col]                  if create_col          else (r[load_col] if load_col else ""),
                        "SPX Pickup Time":     r["SPX Pickup Time"]           if "SPX Pickup Time"   in r.columns else "",
                        "ระยะเวลา (ชม.)":      r[dur_col]                     if dur_col             else "",
                        "WMS Order Key":       r[order_key_col]               if order_key_col       else "",
                        "Brand":               r["Brand No"]                  if "Brand No"          in r.columns else (r["Brand In Article"] if "Brand In Article" in r.columns else ""),
                        "Pallet No":           r["Pallet No"]                 if "Pallet No"         in r.columns else "",
                        "Carrier":             r["Carrier"]                   if "Carrier"           in r.columns else "",
                    })

                def _build_wdcs_unified(df: pd.DataFrame, transport_val: str = "") -> pd.DataFrame:
                    if df.empty:
                        return pd.DataFrame(columns=_UNIFIED_COLS)
                    r = df.reset_index(drop=True)
                    _car = r["Vehicleregistration"] if "Vehicleregistration" in r.columns else ""
                    return pd.DataFrame({
                        "ระบบ":               "WDCS",
                        "TLD / Transport No.": transport_val,
                        "SPX Order SN":        r["Web Order"]         if "Web Order"         in r.columns else "",
                        "SPX Tracking":        r["SPX Tracking"]      if "SPX Tracking"      in r.columns else "",
                        "Create Date (WMS)":   "",
                        "SPX Pickup Time":     r["SPX Pickup Time"]   if "SPX Pickup Time"   in r.columns else "",
                        "ระยะเวลา (ชม.)":      "",
                        "WMS Order Key":       r["Web Order"]         if "Web Order"         in r.columns else "",
                        "Brand":               r["Brand In Article"]  if "Brand In Article"  in r.columns else "",
                        "Pallet No":           "",
                        "Carrier":             _car,
                    })

                with ct_tabs[0]:
                    st.caption("✅ Order ทั้งหมดที่คลังมีบันทึก — ทุก WMS matched รวมในตารางเดียว")
                    unified_parts = []
                    for r, label in results_list:
                        if label == "WDCS":
                            unified_parts.append(_build_wdcs_unified(r.matched_df, transport_val=r.filter_value or ""))
                        else:
                            unified_parts.append(_build_fc_unified(r.matched_df, label, tld_no_val=r.filter_value or ""))
                    if unified_parts:
                        _udf = pd.concat(unified_parts, ignore_index=True)
                        if not _udf.empty:
                            st.dataframe(_udf, use_container_width=True, hide_index=True)
                            st.caption(" | ".join(f"{l}: {len(p)} rows" for p, (_, l) in zip(unified_parts, results_list)) + f" | รวม {len(_udf)} rows")
                        else:
                            st.info("ไม่มี Order ที่ Match")

                if len(results_list) == 2:
                    (r1, l1), (r2, l2) = results_list
                    with ct_tabs[1]:
                        st.caption(f"🟡 พบใน {l1} แต่ไม่พบใน {l2}")
                        if grp_only_1:
                            _col1 = "SPX Order SN" if l1 in ("TLD","FC") else "Web Order"
                            _df1 = r1.matched_df
                            if _col1 in _df1.columns:
                                st.dataframe(_df1[_df1[_col1].isin(grp_only_1)], use_container_width=True, hide_index=True)
                            else:
                                st.dataframe(pd.DataFrame({"Order SN": list(grp_only_1)}), use_container_width=True, hide_index=True)
                        else:
                            st.success(f"🎉 ไม่มี — {l2} มีบันทึกครบ")

                    with ct_tabs[2]:
                        st.caption(f"🟡 พบใน {l2} แต่ไม่พบใน {l1}")
                        if grp_only_2:
                            _col2 = "SPX Order SN" if l2 in ("TLD","FC") else "Web Order"
                            _df2 = r2.matched_df
                            if _col2 in _df2.columns:
                                st.dataframe(_df2[_df2[_col2].isin(grp_only_2)], use_container_width=True, hide_index=True)
                            else:
                                st.dataframe(pd.DataFrame({"Order SN": list(grp_only_2)}), use_container_width=True, hide_index=True)
                        else:
                            st.success(f"🎉 ไม่มี — {l1} มีบันทึกครบ")

                    with ct_tabs[3]:
                        st.caption("🔴 ต้องตรวจสอบเร่งด่วน — SPX รับแต่ไม่พบในทุก WMS")
                        if grp_missing_both:
                            _mdf = results_list[0][0].missing_in_wms_df
                            if spx_col in _mdf.columns:
                                st.dataframe(_mdf[_mdf[spx_col].isin(grp_missing_both)], use_container_width=True, hide_index=True)
                            else:
                                st.dataframe(pd.DataFrame({"Order SN": list(grp_missing_both)}), use_container_width=True, hide_index=True)
                        else:
                            st.success("🎉 ไม่มี — ทุก Order ของ SPX พบในอย่างน้อยหนึ่ง WMS")
                else:
                    with ct_tabs[1]:
                        st.caption("🔴 ต้องตรวจสอบเร่งด่วน — SPX รับแต่ไม่พบในทุก WMS")
                        if grp_missing_both:
                            _mdf = results_list[0][0].missing_in_wms_df
                            if spx_col in _mdf.columns:
                                st.dataframe(_mdf[_mdf[spx_col].isin(grp_missing_both)], use_container_width=True, hide_index=True)
                        else:
                            st.success("🎉 ไม่มี")

        else:
            render_result(tab_obj, slot_type, result, fv)

    # ── Export Excel ──────────────────────────────────────────────────────────
    st.divider()
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        for r, label in results_list:
            s = r.summary
            r.matched_df.to_excel(writer,         sheet_name=f"{label}_Matched",  index=False)
            r.missing_in_wms_df.to_excel(writer,  sheet_name=f"{label}_Missing",  index=False)
            r.extra_in_wms_df.to_excel(writer,    sheet_name=f"{label}_Extra",    index=False)
            pd.DataFrame({
                "Metric": ["Matched", "Missing WMS", "Extra WMS", "Match Rate", "Filter"],
                "Value":  [s["matched"], s["missing_in_wms"], s["extra_in_wms"],
                           f"{s['match_rate']}%", r.filter_value or "(ทั้งไฟล์)"],
            }).to_excel(writer, sheet_name=f"{label}_Summary", index=False)
    buf.seek(0)

    suffix_parts = [r.filter_value for r, _ in results_list if r.filter_value]
    suffix = "_".join(suffix_parts) if suffix_parts else "all"

    st.download_button(
        "📥 Download Excel Report",
        buf,
        file_name=f"reconciliation_SPX_{suffix}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


elif not carrier_files and not any_wms:
    st.markdown("""
### วิธีใช้งาน v2.0
1. **อัปโหลด SPX** — เลือกหลาย PDF ได้พร้อมกัน (Ctrl+click)
2. **อัปโหลด WMS** — TLD Report (.xls) เป็นหลัก + FC Export DO (.xlsx) / WDCS (.txt) เสริม
3. **เลือกมุมมอง** — SPX เป็นหลัก หรือ WMS เป็นหลัก
4. กด **Reconcile** → ดูผลแยก TLD | FC | WDCS | Combined

---
> - ⚠️ **Missing in WMS** = 3PL รับแต่คลังไม่มีบันทึก *(SPX Master)*
> - ⚠️ **Extra in WMS** = คลังปล่อยแต่ 3PL ไม่ได้รับ *(WMS Master)*
> - 🔴 **Missing ทั้งหมด** = ต้องตรวจสอบเร่งด่วน
""")