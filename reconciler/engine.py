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
    Carrier: SPX → key = order_sn
    WMS:     FC  → key = Weborder DO  (primary)
                   3PL Transport Tracking No → SPX Tracking (if available)
    Filter:  Truck Load No
    """
    spx_keys = set(spx_df["order_sn"].dropna().str.strip().replace("", pd.NA).dropna())

    fc_weborder = fc_df["Weborder DO"].dropna().str.strip()
    fc_keys = set(fc_weborder[fc_weborder != ""])

    matched_keys = spx_keys & fc_keys
    missing_in_wms_keys = spx_keys - fc_keys
    extra_in_wms_keys = fc_keys - spx_keys

    # FC display columns
    fc_cols = ["Weborder DO"] + [c for c in ["Brand In Article", "Total Box", "Pick Qty",
                                              "3PL Transport Tracking No", "Carrier", "Truck Load No"]
                                  if c in fc_df.columns]

    # Matched
    matched_fc = fc_df[fc_df["Weborder DO"].isin(matched_keys)][fc_cols].copy()
    matched_spx = spx_df[spx_df["order_sn"].isin(matched_keys)][["order_sn", "tracking"]].copy()
    matched_spx.columns = ["Weborder DO", "SPX Tracking"]
    matched_df = matched_fc.merge(matched_spx, on="Weborder DO", how="left")
    matched_df.insert(0, "Status", "Matched ✅")

    # Missing in WMS
    miss_wms_df = spx_df[spx_df["order_sn"].isin(missing_in_wms_keys)][["order_sn", "tracking", "pickup_time"]].copy()
    miss_wms_df.columns = ["Order SN (SPX)", "SPX Tracking", "Pickup Time"]
    miss_wms_df.insert(0, "Status", "Missing in WMS ⚠️")

    # Extra in WMS
    extra_wms_df = fc_df[fc_df["Weborder DO"].isin(extra_in_wms_keys)][fc_cols].copy()
    extra_wms_df.insert(0, "Status", "Extra in WMS ⚠️")

    total_carrier = len(spx_keys)
    summary = {
        "carrier_total": total_carrier,
        "wms_total": len(fc_keys),
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
        wms_type="FC",
        filter_key="Truck Load No.",
        filter_value=truck_load_no,
    )