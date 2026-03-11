import sys, os, traceback

OUT = r"d:\Users\sanonpawit\OneDrive - Central Group\Transport_Project_allocation\output\app\result.txt"

try:
    sys.path.insert(0, r"d:\Users\sanonpawit\OneDrive - Central Group\Transport_Project_allocation\output\app")
    import pandas as pd
    from reconciler.engine import reconcile_spx_fc

    spx_df = pd.DataFrame([
        {"tracking": "TH2632663830599", "order_sn": "260309NSTNP61R",  "pickup_time": "x"},
        {"tracking": "TH9999999999999", "order_sn": "260309NN4RR6E2",  "pickup_time": "x"},
    ])
    # Real FC rows: SPX orders have NO Weborder DO (blank)
    fc_df = pd.DataFrame([
        {"Weborder DO": "",   "3PL Transport Tracking No": "TH2632663830599", "Order no": "CMGSHP306727483-260309NSTNP61R-01"},
        {"Weborder DO": None, "3PL Transport Tracking No": None,              "Order no": "CMGSHP80938469-260309NN4RR6E2-01"},
    ])

    r = reconcile_spx_fc(spx_df, fc_df)
    msg = (
        f"matched={r.summary['matched']}\n"
        f"missing={r.summary['missing_in_wms']}\n"
        f"matched_by_tracking={r.summary['matched_by_tracking']}\n"
        f"matched_by_orderkey={r.summary['matched_by_orderkey']}\n"
        f"matched_order_nos={r.matched_df['Order no'].tolist() if not r.matched_df.empty else []}\n"
        f"missing_orders={r.missing_in_wms_df['Order SN (SPX)'].tolist() if not r.missing_in_wms_df.empty else []}\n"
    )
except Exception:
    msg = traceback.format_exc()

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(msg)
    fh.flush()