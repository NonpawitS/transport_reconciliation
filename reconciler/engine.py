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
    wdcs_cols = ["Web Order"] + [c for c in ["Brand In Article", "TotalBox", "SumOfPickQty", "Transport_No"] if c in wdcs_df.columns]

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
    WMS:     FC  → 3PL Transport Tracking No (primary, if available)
                   Weborder DO (fallback)

    Match priority per SPX row:
      1. SPX tracking → FC 3PL Transport Tracking No  (tracking-to-tracking)
      2. SPX order_sn → FC Weborder DO               (order-to-weborder fallback)
    """
    fc_cols_display = ["Weborder DO"] + [c for c in [
        "3PL Transport Tracking No", "Brand In Article", "Total Box",
        "Pick Qty", "Carrier", "Truck Load No",
    ] if c in fc_df.columns]

    # ── Build FC lookup sets ──────────────────────────────────────────────────
    fc_tracking_col = "3PL Transport Tracking No"
    fc_has_tracking = (
        fc_tracking_col in fc_df.columns
        and fc_df[fc_tracking_col].dropna().str.strip().replace("", pd.NA).dropna().shape[0] > 0
    )

    # tracking → Weborder DO (for merging results)
    fc_tracking_to_weborder: dict[str, str] = {}
    if fc_has_tracking:
        tmp = fc_df[[fc_tracking_col, "Weborder DO"]].dropna(subset=[fc_tracking_col])
        tmp = tmp[tmp[fc_tracking_col].str.strip() != ""]
        fc_tracking_to_weborder = dict(zip(tmp[fc_tracking_col].str.strip(), tmp["Weborder DO"].str.strip()))

    fc_weborder_set = set(fc_df["Weborder DO"].dropna().str.strip().replace("", pd.NA).dropna())

    # ── Classify each SPX order ───────────────────────────────────────────────
    matched_by_tracking = []   # (spx_row, match_method)
    matched_by_orderkey = []
    missing_rows = []

    for _, row in spx_df.iterrows():
        tracking = str(row.get("tracking", "") or "").strip()
        order_sn = str(row.get("order_sn", "") or "").strip()

        if fc_has_tracking and tracking and tracking in fc_tracking_to_weborder:
            matched_by_tracking.append(row)
        elif order_sn and order_sn in fc_weborder_set:
            matched_by_orderkey.append(row)
        else:
            missing_rows.append(row)

    matched_spx_df = pd.DataFrame(matched_by_tracking + matched_by_orderkey)
    missing_spx_df = pd.DataFrame(missing_rows)

    # ── Build matched_df ──────────────────────────────────────────────────────
    matched_weborders: set[str] = set()

    # From tracking matches → get their weborder_do
    tracking_weborders = {fc_tracking_to_weborder[r["tracking"]]
                          for r in matched_by_tracking
                          if r["tracking"] in fc_tracking_to_weborder}
    # From order_sn matches
    orderkey_weborders = {r["order_sn"] for r in matched_by_orderkey}

    matched_weborders = tracking_weborders | orderkey_weborders

    matched_fc = fc_df[fc_df["Weborder DO"].isin(matched_weborders)][fc_cols_display].copy()

    # Merge SPX info
    spx_for_merge = matched_spx_df[["order_sn", "tracking"]].copy() if not matched_spx_df.empty else pd.DataFrame(columns=["order_sn", "tracking"])
    spx_for_merge = spx_for_merge.rename(columns={"order_sn": "Weborder DO", "tracking": "SPX Tracking"})
    matched_df = matched_fc.merge(spx_for_merge, on="Weborder DO", how="left")
    matched_df.insert(0, "Match Method",
                      matched_df["Weborder DO"].apply(
                          lambda w: "Tracking" if w in tracking_weborders else "Weborder DO"
                      ))
    matched_df.insert(0, "Status", "Matched ✅")

    # ── Missing in WMS ────────────────────────────────────────────────────────
    if missing_spx_df.empty:
        miss_wms_df = pd.DataFrame(columns=["Status", "Order SN (SPX)", "SPX Tracking", "Pickup Time"])
    else:
        miss_wms_df = missing_spx_df[["order_sn", "tracking", "pickup_time"]].copy()
        miss_wms_df.columns = ["Order SN (SPX)", "SPX Tracking", "Pickup Time"]
        miss_wms_df.insert(0, "Status", "Missing in WMS ⚠️")

    # ── Extra in WMS (FC orders not picked by any SPX row) ────────────────────
    extra_weborders = fc_weborder_set - matched_weborders
    extra_wms_df = fc_df[fc_df["Weborder DO"].isin(extra_weborders)][fc_cols_display].copy()
    extra_wms_df.insert(0, "Status", "Extra in WMS ⚠️")

    total_carrier = len(spx_df)
    n_matched = len(matched_by_tracking) + len(matched_by_orderkey)
    summary = {
        "carrier_total": total_carrier,
        "wms_total": len(fc_weborder_set),
        "matched": n_matched,
        "matched_by_tracking": len(matched_by_tracking),
        "matched_by_orderkey": len(matched_by_orderkey),
        "missing_in_wms": len(missing_rows),
        "extra_in_wms": len(extra_weborders),
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