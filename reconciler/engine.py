"""
Reconciliation Engine
Carrier = Master (what 3PL picked up)
WMS = Filtered by Transport No. / Truck Load No. (what warehouse released)
Goal: verify what 3PL picked == what warehouse released

Supported combinations:
  SPX + WDCS : Order SN → Web Order
  SPX + FC   : Order SN → Weborder DO  (tracking fallback if available)
"""
import pandas as pd
from dataclasses import dataclass


def find_matching_transports(
    carrier_keys: set,
    wms_df: pd.DataFrame,
    filter_col: str,
    match_col: str,
) -> list[dict]:
    """
    Scan WMS file to find which Transport_No / Truck Load No values
    have orders overlapping with the carrier file.
    Returns list sorted by match_count descending.
    """
    if filter_col not in wms_df.columns or match_col not in wms_df.columns:
        return []

    results = []
    for transport_val, group in wms_df.groupby(filter_col):
        val_str = str(transport_val).strip()
        if not val_str or val_str == "nan":
            continue
        wms_keys = set(group[match_col].dropna().str.strip().replace("", pd.NA).dropna())
        match_count = len(carrier_keys & wms_keys)
        results.append({
            "transport_no": val_str,
            "match_count": match_count,
            "total": len(wms_keys),
        })

    results.sort(key=lambda x: x["match_count"], reverse=True)
    return results


@dataclass
class ReconciliationResult:
    summary: dict
    matched_df: pd.DataFrame
    missing_in_wms_df: pd.DataFrame       # In Carrier, NOT in WMS — 3PL picked but WMS has no record
    extra_in_wms_df: pd.DataFrame         # In WMS, NOT in Carrier — WMS released but 3PL didn't pick
    carrier_type: str
    wms_type: str
    filter_key: str = ""
    filter_value: str = ""


# ─── SPX + WDCS ──────────────────────────────────────────────────────────────

def reconcile_spx_wdcs(
    spx_df: pd.DataFrame,
    wdcs_df: pd.DataFrame,
    transport_no: str = "",
) -> ReconciliationResult:
    """
    Carrier: SPX  → key = order_sn
    WMS:     WDCS → key = Web Order
    Filter:  Transport_No
    """
    spx_keys = set(spx_df["order_sn"].dropna().str.strip().replace("", pd.NA).dropna())
    wdcs_keys = set(wdcs_df["Web Order"].dropna().str.strip().replace("", pd.NA).dropna())

    matched_keys = spx_keys & wdcs_keys
    missing_in_wms_keys = spx_keys - wdcs_keys       # Carrier picked, WMS missing
    extra_in_wms_keys = wdcs_keys - spx_keys          # WMS released, Carrier didn't pick

    # WMS display columns
    wdcs_cols = ["Web Order"] + [c for c in [
        "Brand In Article", "TotalBox", "SumOfPickQty", "Transport_No", "Vehicleregistration",
    ] if c in wdcs_df.columns]

    # Matched
    matched_wdcs = wdcs_df[wdcs_df["Web Order"].isin(matched_keys)][wdcs_cols].copy()
    matched_spx = spx_df[spx_df["order_sn"].isin(matched_keys)][["order_sn", "tracking"]].copy()
    matched_spx.columns = ["Web Order", "SPX Tracking"]
    matched_df = matched_wdcs.merge(matched_spx, on="Web Order", how="left")
    matched_df.insert(0, "Status", "Matched ✅")

    # Missing in WMS (3PL picked but WMS has no record)
    miss_wms_df = spx_df[spx_df["order_sn"].isin(missing_in_wms_keys)][["order_sn", "tracking", "pickup_time"]].copy()
    miss_wms_df.columns = ["Order SN (SPX)", "SPX Tracking", "Pickup Time"]
    miss_wms_df.insert(0, "Status", "Missing in WMS ⚠️")

    # Extra in WMS (WMS released but 3PL didn't pick)
    extra_wms_df = wdcs_df[wdcs_df["Web Order"].isin(extra_in_wms_keys)][wdcs_cols].copy()
    extra_wms_df.insert(0, "Status", "Extra in WMS ⚠️")

    total_carrier = len(spx_keys)
    summary = {
        "carrier_total": total_carrier,
        "wms_total": len(wdcs_keys),
        "matched": len(matched_keys),
        "missing_in_wms": len(missing_in_wms_keys),
        "extra_in_wms": len(extra_in_wms_keys),
        "match_rate": round(len(matched_keys) / total_carrier * 100, 1) if total_carrier else 0,
    }

    return ReconciliationResult(
        summary=summary,
        matched_df=matched_df,
        missing_in_wms_df=miss_wms_df,
        extra_in_wms_df=extra_wms_df,
        carrier_type="SPX",
        wms_type="WDCS",
        filter_key="Transport No.",
        filter_value=transport_no,
    )


# ─── SPX + FC ────────────────────────────────────────────────────────────────

def reconcile_spx_fc(
    spx_df: pd.DataFrame,
    fc_df: pd.DataFrame,
    truck_load_no: str = "",
) -> ReconciliationResult:
    """
    Carrier: SPX → tracking (TH...) and order_sn
    WMS:     FC  → 3PL Transport Tracking No (primary)
                   Order no segment fallback (SPX rows have no Weborder DO)

    FC row key = "Order no" (e.g. "CMGSHP306727483-260309NSTNP61R-01")
    Match priority per SPX row:
      1. SPX tracking == FC 3PL Transport Tracking No
      2. SPX order_sn found as segment inside FC Order no
    """
    fc_order_col = "Order no"
    fc_tracking_col = "3PL Transport Tracking No"

    fc_cols_display = [fc_order_col] + [c for c in [
        "Weborder DO", "3PL Transport Tracking No", "Brand In Article",
        "Total Box", "Pick Qty", "Carrier", "Truck Load No",
    ] if c in fc_df.columns]

    # ── Check if FC has 3PL tracking data ────────────────────────────────────
    fc_has_tracking = (
        fc_tracking_col in fc_df.columns
        and fc_df[fc_tracking_col].dropna().str.strip().replace("", pd.NA).dropna().shape[0] > 0
    )

    # ── tracking → Order no ───────────────────────────────────────────────────
    fc_tracking_to_order_no: dict[str, str] = {}
    if fc_has_tracking and fc_order_col in fc_df.columns:
        tmp = fc_df[[fc_tracking_col, fc_order_col]].dropna(subset=[fc_tracking_col, fc_order_col])
        tmp = tmp[tmp[fc_tracking_col].astype(str).str.strip().ne("")]
        for _, r in tmp.iterrows():
            trk = str(r[fc_tracking_col]).strip()
            ono = str(r[fc_order_col]).strip()
            if trk and trk != "nan" and ono and ono != "nan":
                fc_tracking_to_order_no[trk] = ono
        fc_tracking_to_order_no.pop("nan", None)
        fc_tracking_to_order_no.pop("", None)

    # ── orderkey (shopee SN segment) → Order no ───────────────────────────────
    # FC Order no = "CMGSHP{numeric}-{shopee_SN}-{seq}"
    # Extract each '-'-separated segment ≥6 chars as a lookup key
    fc_orderkey_to_order_no: dict[str, str] = {}

    def _index_order_string(order_str: str, order_no_key: str) -> None:
        s = str(order_str).strip()
        if not s or s == "nan":
            return
        if s not in fc_orderkey_to_order_no:
            fc_orderkey_to_order_no[s] = order_no_key
        for part in s.split("-"):
            p = part.strip()
            if p and p != "nan" and len(p) >= 6 and p not in fc_orderkey_to_order_no:
                fc_orderkey_to_order_no[p] = order_no_key

    if fc_order_col in fc_df.columns:
        for order_no in fc_df[fc_order_col].dropna():
            ono = str(order_no).strip()
            if ono and ono != "nan":
                _index_order_string(ono, ono)

    # ── Classify each SPX order ───────────────────────────────────────────────
    matched_by_tracking = []
    matched_by_orderkey = []
    missing_rows = []

    for _, row in spx_df.iterrows():
        tracking = str(row.get("tracking", "") or "").strip()
        order_sn = str(row.get("order_sn", "") or "").strip()

        if fc_has_tracking and tracking and tracking in fc_tracking_to_order_no:
            matched_by_tracking.append(row)
        elif order_sn and order_sn in fc_orderkey_to_order_no:
            matched_by_orderkey.append(row)
        else:
            missing_rows.append(row)

    # ── Collect matched FC Order no values ───────────────────────────────────
    tracking_order_nos = {
        fc_tracking_to_order_no[str(r["tracking"]).strip()]
        for r in matched_by_tracking
        if str(r["tracking"]).strip() in fc_tracking_to_order_no
    }
    orderkey_order_nos = {
        fc_orderkey_to_order_no[str(r["order_sn"]).strip()]
        for r in matched_by_orderkey
        if str(r["order_sn"]).strip() in fc_orderkey_to_order_no
    }
    matched_order_nos = tracking_order_nos | orderkey_order_nos

    # ── Build matched_df ──────────────────────────────────────────────────────
    if fc_order_col in fc_df.columns and matched_order_nos:
        matched_fc = fc_df[fc_df[fc_order_col].isin(matched_order_nos)][fc_cols_display].copy()
    else:
        matched_fc = pd.DataFrame(columns=fc_cols_display)

    spx_merge_rows = []
    for r in matched_by_tracking:
        trk = str(r["tracking"]).strip()
        ono = fc_tracking_to_order_no.get(trk, "")
        if ono:
            spx_merge_rows.append({fc_order_col: ono, "SPX Tracking": trk, "SPX Order SN": str(r["order_sn"]).strip()})
    for r in matched_by_orderkey:
        osn = str(r["order_sn"]).strip()
        ono = fc_orderkey_to_order_no.get(osn, "")
        if ono:
            spx_merge_rows.append({fc_order_col: ono, "SPX Tracking": str(r["tracking"]).strip(), "SPX Order SN": osn})

    spx_merge_df = (
        pd.DataFrame(spx_merge_rows)
        if spx_merge_rows
        else pd.DataFrame(columns=[fc_order_col, "SPX Tracking", "SPX Order SN"])
    )
    if not matched_fc.empty:
        matched_df = matched_fc.merge(spx_merge_df, on=fc_order_col, how="left")
        matched_df.insert(0, "Match Method",
                          matched_df[fc_order_col].apply(
                              lambda o: "Tracking" if o in tracking_order_nos else "Order SN"
                          ))
        matched_df.insert(0, "Status", "Matched ✅")
    else:
        matched_df = pd.DataFrame()

    # ── Missing in WMS ────────────────────────────────────────────────────────
    if missing_rows:
        miss_wms_df = pd.DataFrame(missing_rows)[["order_sn", "tracking", "pickup_time"]].copy()
        miss_wms_df.columns = ["Order SN (SPX)", "SPX Tracking", "Pickup Time"]
        miss_wms_df.insert(0, "Status", "Missing in WMS ⚠️")
    else:
        miss_wms_df = pd.DataFrame(columns=["Status", "Order SN (SPX)", "SPX Tracking", "Pickup Time"])

    # ── Extra in WMS (FC orders not matched by any SPX) ───────────────────────
    if fc_order_col in fc_df.columns:
        fc_all_order_nos = (
            set(fc_df[fc_order_col].dropna().astype(str).str.strip()) - {"", "nan"}
        )
    else:
        fc_all_order_nos = set()

    extra_order_nos = fc_all_order_nos - matched_order_nos
    if fc_order_col in fc_df.columns and extra_order_nos:
        extra_wms_df = fc_df[fc_df[fc_order_col].isin(extra_order_nos)][fc_cols_display].copy()
        extra_wms_df.insert(0, "Status", "Extra in WMS ⚠️")
    else:
        extra_wms_df = pd.DataFrame()

    total_carrier = len(spx_df)
    n_matched = len(matched_by_tracking) + len(matched_by_orderkey)
    summary = {
        "carrier_total": total_carrier,
        "wms_total": len(fc_all_order_nos),
        "matched": n_matched,
        "matched_by_tracking": len(matched_by_tracking),
        "matched_by_orderkey": len(matched_by_orderkey),
        "missing_in_wms": len(missing_rows),
        "extra_in_wms": len(extra_order_nos),
        "match_rate": round(n_matched / total_carrier * 100, 1) if total_carrier else 0,
        "fc_has_tracking": fc_has_tracking,
    }

    return ReconciliationResult(
        summary=summary,
        matched_df=matched_df,
        missing_in_wms_df=miss_wms_df,
        extra_in_wms_df=extra_wms_df,
        carrier_type="SPX",
        wms_type="FC",
        filter_key="Truck Load No.",
        filter_value=truck_load_no,
    )


# ─── SPX + TLD Report ────────────────────────────────────────────────────────

def reconcile_spx_tld(
    spx_df: pd.DataFrame,
    tld_df: pd.DataFrame,
    tld_no: str = "",
) -> "ReconciliationResult":
    """
    Carrier: SPX  → tracking (TH...) and order_sn
    WMS:     TLD Report → Tracking Number (primary), Order No segment (fallback)

    TLD row key = "Order No" (e.g. "CMGSHP168415662-260310RNDX7YBB-01")
    Match priority per SPX row:
      1. SPX tracking == TLD Tracking Number
      2. SPX order_sn found as segment inside TLD Order No
    """
    tld_tracking_col = "Tracking Number"
    tld_order_col    = "Order No"

    tld_cols_display = [tld_order_col] + [c for c in [
        "Tracking Number", "Brand No", "Pallet No", "Carton No", "Handover date&time",
    ] if c in tld_df.columns]

    # ── tracking → Order No lookup ────────────────────────────────────────────
    tld_tracking_to_order_no: dict[str, str] = {}
    if tld_tracking_col in tld_df.columns and tld_order_col in tld_df.columns:
        tmp = tld_df[[tld_tracking_col, tld_order_col]].dropna(subset=[tld_tracking_col, tld_order_col])
        tmp = tmp[tmp[tld_tracking_col].astype(str).str.strip().ne("")]
        for _, r in tmp.iterrows():
            trk = str(r[tld_tracking_col]).strip()
            ono = str(r[tld_order_col]).strip()
            if trk and trk != "nan" and ono and ono != "nan":
                tld_tracking_to_order_no[trk] = ono

    # ── orderkey (shopee SN segment) → Order No lookup ────────────────────────
    tld_orderkey_to_order_no: dict[str, str] = {}

    def _index_tld_order(order_str: str, order_no_key: str) -> None:
        s = str(order_str).strip()
        if not s or s == "nan":
            return
        if s not in tld_orderkey_to_order_no:
            tld_orderkey_to_order_no[s] = order_no_key
        for part in s.split("-"):
            p = part.strip()
            if p and p != "nan" and len(p) >= 6 and p not in tld_orderkey_to_order_no:
                tld_orderkey_to_order_no[p] = order_no_key

    if tld_order_col in tld_df.columns:
        for order_no in tld_df[tld_order_col].dropna():
            ono = str(order_no).strip()
            if ono and ono != "nan":
                _index_tld_order(ono, ono)

    # ── Classify each SPX order ───────────────────────────────────────────────
    matched_by_tracking = []
    matched_by_orderkey = []
    missing_rows = []

    for _, row in spx_df.iterrows():
        tracking  = str(row.get("tracking",  "") or "").strip()
        order_sn  = str(row.get("order_sn",  "") or "").strip()

        if tracking and tracking in tld_tracking_to_order_no:
            matched_by_tracking.append(row)
        elif order_sn and order_sn in tld_orderkey_to_order_no:
            matched_by_orderkey.append(row)
        else:
            missing_rows.append(row)

    # ── Collect matched TLD Order No values ───────────────────────────────────
    tracking_order_nos = {
        tld_tracking_to_order_no[str(r["tracking"]).strip()]
        for r in matched_by_tracking
        if str(r["tracking"]).strip() in tld_tracking_to_order_no
    }
    orderkey_order_nos = {
        tld_orderkey_to_order_no[str(r["order_sn"]).strip()]
        for r in matched_by_orderkey
        if str(r["order_sn"]).strip() in tld_orderkey_to_order_no
    }
    matched_order_nos = tracking_order_nos | orderkey_order_nos

    # ── Build matched_df ──────────────────────────────────────────────────────
    if tld_order_col in tld_df.columns and matched_order_nos:
        matched_tld = tld_df[tld_df[tld_order_col].isin(matched_order_nos)][tld_cols_display].copy()
    else:
        matched_tld = pd.DataFrame(columns=tld_cols_display)

    spx_merge_rows = []
    for r in matched_by_tracking:
        trk = str(r["tracking"]).strip()
        ono = tld_tracking_to_order_no.get(trk, "")
        if ono:
            spx_merge_rows.append({tld_order_col: ono, "SPX Tracking": trk, "SPX Order SN": str(r["order_sn"]).strip()})
    for r in matched_by_orderkey:
        osn = str(r["order_sn"]).strip()
        ono = tld_orderkey_to_order_no.get(osn, "")
        if ono:
            spx_merge_rows.append({tld_order_col: ono, "SPX Tracking": str(r["tracking"]).strip(), "SPX Order SN": osn})

    spx_merge_df = (
        pd.DataFrame(spx_merge_rows)
        if spx_merge_rows
        else pd.DataFrame(columns=[tld_order_col, "SPX Tracking", "SPX Order SN"])
    )
    if not matched_tld.empty:
        matched_df = matched_tld.merge(spx_merge_df, on=tld_order_col, how="left")
        matched_df.insert(0, "Match Method",
                          matched_df[tld_order_col].apply(
                              lambda o: "Tracking" if o in tracking_order_nos else "Order SN"
                          ))
        matched_df.insert(0, "Status", "Matched ✅")
    else:
        matched_df = pd.DataFrame()

    # ── Missing in WMS ────────────────────────────────────────────────────────
    if missing_rows:
        miss_wms_df = pd.DataFrame(missing_rows)[["order_sn", "tracking", "pickup_time"]].copy()
        miss_wms_df.columns = ["Order SN (SPX)", "SPX Tracking", "Pickup Time"]
        miss_wms_df.insert(0, "Status", "Missing in WMS ⚠️")
    else:
        miss_wms_df = pd.DataFrame(columns=["Status", "Order SN (SPX)", "SPX Tracking", "Pickup Time"])

    # ── Extra in WMS (TLD orders not matched by any SPX) ─────────────────────
    if tld_order_col in tld_df.columns:
        tld_all_order_nos = set(tld_df[tld_order_col].dropna().astype(str).str.strip()) - {"", "nan"}
    else:
        tld_all_order_nos = set()

    extra_order_nos = tld_all_order_nos - matched_order_nos
    if tld_order_col in tld_df.columns and extra_order_nos:
        extra_wms_df = tld_df[tld_df[tld_order_col].isin(extra_order_nos)][tld_cols_display].copy()
        extra_wms_df.insert(0, "Status", "Extra in WMS ⚠️")
    else:
        extra_wms_df = pd.DataFrame()

    total_carrier = len(spx_df)
    n_matched = len(matched_by_tracking) + len(matched_by_orderkey)
    summary = {
        "carrier_total": total_carrier,
        "wms_total": len(tld_all_order_nos),
        "matched": n_matched,
        "matched_by_tracking": len(matched_by_tracking),
        "matched_by_orderkey": len(matched_by_orderkey),
        "missing_in_wms": len(missing_rows),
        "extra_in_wms": len(extra_order_nos),
        "match_rate": round(n_matched / total_carrier * 100, 1) if total_carrier else 0,
    }

    return ReconciliationResult(
        summary=summary,
        matched_df=matched_df,
        missing_in_wms_df=miss_wms_df,
        extra_in_wms_df=extra_wms_df,
        carrier_type="SPX",
        wms_type="TLD",
        filter_key="Truck Load No.",
        filter_value=tld_no,
    )