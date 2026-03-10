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

# ── Step 2: Transport / Load No. ──────────────────────────────────────────────
filter_value = ""
both_ready = carrier_file and wms_file and carrier_type == "SPX" and wms_type in ("WDCS", "FC")

if both_ready:
    st.divider()
    st.subheader("Step 2 — เลือก Transport / Load No.")

    filter_col = "Transport_No" if wms_type == "WDCS" else "Truck Load No"
    match_col  = "Web Order"    if wms_type == "WDCS" else "Weborder DO"
    label_name = "Transport No. (WDCS)" if wms_type == "WDCS" else "Truck Load No. (FC)"

    # ── Auto-detect: parse both files and find matching transports ────────────
    cache_key = f"transport_options_{carrier_file.name}_{wms_file.name}"
    if cache_key not in st.session_state:
        with st.spinner("🔍 วิเคราะห์ไฟล์ — หา Transport ที่ตรงกับ Carrier..."):
            try:
                carrier_file.seek(0)
                _, carrier_keys = get_carrier_keys(carrier_file.read(), carrier_type)
                carrier_file.seek(0)

                wms_file.seek(0)
                wms_full_df = get_wms_df_full(wms_file.read(), wms_type)
                wms_file.seek(0)

                matches = find_matching_transports(carrier_keys, wms_full_df, filter_col, match_col)
                st.session_state[cache_key] = matches
                st.session_state[f"carrier_keys_{cache_key}"] = carrier_keys
            except Exception as e:
                st.error(f"❌ วิเคราะห์ไฟล์ไม่สำเร็จ: {e}")
                st.session_state[cache_key] = []

    matches = st.session_state.get(cache_key, [])

    # ── Build option labels ───────────────────────────────────────────────────
    # Format: "3260001318  ✅ 115 match / 115 total" or "3260001030  — 0 match / 42 total"
    def make_label(m: dict) -> str:
        icon = "✅" if m["match_count"] > 0 else "  "
        return f"{m['transport_no']}  {icon} {m['match_count']} match / {m['total']} total"

    all_labels = [make_label(m) for m in matches]
    all_values = [m["transport_no"] for m in matches]
    matched_labels = [l for l, m in zip(all_labels, matches) if m["match_count"] > 0]
    matched_values = [v for v, m in zip(all_values, matches) if m["match_count"] > 0]

    # ── Show summary banner ───────────────────────────────────────────────────
    n_with_match = len(matched_values)
    if n_with_match:
        st.success(f"✅ พบ **{n_with_match}** Transport/Load ที่มี orders ตรงกับ Carrier ({len(all_values)} ทั้งหมด)")
    else:
        st.warning("⚠️ ไม่พบ Transport/Load ที่ตรงกับ Carrier — ตรวจสอบช่วงวันที่ของไฟล์")

    # ── Barcode scan / text search ────────────────────────────────────────────
    col_scan, col_dropdown = st.columns([2, 3])

    with col_scan:
        st.markdown("**Scan หรือพิมพ์ค้นหา** *(wildcard)*")
        search_text = st.text_input(
            label_name,
            key="transport_search",
            placeholder="Scan barcode หรือพิมพ์บางส่วน เช่น 3260",
            help="รองรับ Barcode Scanner — เมื่อ Scan แล้วกด Enter ระบบจะกรองให้อัตโนมัติ",
        )

    with col_dropdown:
        st.markdown("**เลือกจาก Dropdown** *(✅ = ตรงกับ Carrier)*")

        # Filter options by search text (wildcard)
        if search_text.strip():
            search_lower = search_text.strip().lower()
            filtered_labels = [l for l, v in zip(all_labels, all_values) if search_lower in v.lower()]
            filtered_values = [v for v in all_values if search_lower in v.lower()]
        else:
            # Show matched first, then rest
            unmatched_labels = [l for l, m in zip(all_labels, matches) if m["match_count"] == 0]
            unmatched_values = [v for v, m in zip(all_values, matches) if m["match_count"] == 0]
            filtered_labels = matched_labels + unmatched_labels
            filtered_values = matched_values + unmatched_values

        if filtered_labels:
            # Pre-select: if search text exactly matches a value, auto-select it
            default_idx = 0
            if search_text.strip() and search_text.strip() in filtered_values:
                default_idx = filtered_values.index(search_text.strip())

            selected_label = st.selectbox(
                "เลือก Transport / Load No.",
                options=filtered_labels,
                index=default_idx,
                key="transport_select",
                label_visibility="collapsed",
            )
            # Extract value from label (before first space)
            filter_value = selected_label.split(" ")[0] if selected_label else ""
        else:
            st.warning(f"ไม่พบ '{search_text}' — ลองพิมพ์ใหม่")
            filter_value = search_text.strip()

    # Show selected info
    if filter_value:
        match_info = next((m for m in matches if m["transport_no"] == filter_value), None)
        if match_info and match_info["match_count"] > 0:
            st.info(f"🔖 เลือก: **{filter_value}** — มี {match_info['match_count']} orders ตรงกับ Carrier จาก {match_info['total']} orders ทั้งหมด")
        else:
            st.caption(f"🔖 เลือก: **{filter_value}**")

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
    with st.spinner("กำลังประมวลผล..."):

        carrier_file.seek(0)
        try:
            spx_df = parse_spx_pdf(carrier_file.read())
            ok, err = validate_spx_df(spx_df)
            if not ok:
                st.error(f"❌ SPX Parser Error: {err}")
                st.stop()
        except Exception as e:
            st.error(f"❌ ไม่สามารถอ่าน SPX PDF: {e}")
            st.stop()

        wms_file.seek(0)
        try:
            if wms_type == "WDCS":
                wdcs_df = parse_wdcs_txt(wms_file.read(), transport_no=filter_value)
                ok2, err2 = validate_wdcs_df(wdcs_df, filter_value)
                if not ok2:
                    st.error(f"❌ WDCS: {err2}")
                    st.stop()
                result = reconcile_spx_wdcs(spx_df, wdcs_df, transport_no=filter_value)
            else:
                fc_df = parse_fc_xlsx(wms_file.read(), truck_load_no=filter_value)
                ok2, err2 = validate_fc_df(fc_df, filter_value)
                if not ok2:
                    st.error(f"❌ FC: {err2}")
                    st.stop()
                result = reconcile_spx_fc(spx_df, fc_df, truck_load_no=filter_value)
        except Exception as e:
            st.error(f"❌ ไม่สามารถ Reconcile: {e}")
            st.stop()

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