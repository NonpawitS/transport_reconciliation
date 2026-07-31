"""
Reconciliation Engine
Carrier = Master (what 3PL picked up)
WMS = Filtered by Transport No. / Truck Load No. (what warehouse released)
Goal: verify what 3PL picked == what warehouse released

Reconcile:  SPX ↔ TLD Report  — Tracking Number (Order SN segment เป็น fallback)
Enrichment: FC Export DO เติมข้อมูลเข้าผลลัพธ์ ไม่ได้เอามาเทียบ
"""
import re
import pandas as pd
from dataclasses import dataclass


def _fmt_dt(v) -> str:
    """Format datetime value → dd/mm/yyyy HH:MM  (data assumed Bangkok UTC+7)"""
    try:
        s = str(v).strip()
        if not s or s == "nan":
            return ""
        return pd.to_datetime(s).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(v) if v and str(v) != "nan" else ""


def _delta_hours(t_create, t_pickup) -> str:
    """
    Elapsed time: t_pickup - t_create → 'XX ชม. X นาที'
    Negative (3PL picked before WMS record) → '-XX ชม. X นาที'
    """
    try:
        s1 = str(t_create).strip()
        s2 = str(t_pickup).strip()
        if not s1 or s1 == "nan" or not s2 or s2 == "nan":
            return ""
        total_min = int(round((pd.to_datetime(s2) - pd.to_datetime(s1)).total_seconds() / 60))
        sign = "-" if total_min < 0 else ""
        total_min = abs(total_min)
        h, m = divmod(total_min, 60)
        return f"{sign}{h} ชม. {m} นาที"
    except Exception:
        return ""


_MISSING_COLS = ["Status", "SPX TO No.", "Order SN (SPX)", "SPX Tracking", "Pickup Time"]


def match_key(df: pd.DataFrame, sn_col: str, trk_col: str) -> pd.Series:
    """
    คีย์สำหรับเทียบรายการข้ามระบบ / ตัดรายการซ้ำ
    ใช้ Order SN ถ้ามีค่า ไม่มีก็ fallback เป็น 'TRK:<tracking>'
    (ไฟล์ SPX Transport Order .xlsx ไม่มี Order SN จึงต้องพึ่ง Tracking Number)
    คืน "" เมื่อไม่มีทั้งสองค่า
    """
    if df.empty:
        return pd.Series([], dtype="object")

    def _col(name: str) -> pd.Series:
        if name in df.columns:
            return df[name].map(_clean_str)
        return pd.Series([""] * len(df), index=df.index)

    sn, trk = _col(sn_col), _col(trk_col)
    return sn.where(sn.ne(""), ("TRK:" + trk).where(trk.ne(""), ""))


def _clean_str(v) -> str:
    """str() + strip + แปลง missing ทุกรูปแบบ → '' (pandas 3 ใช้ pd.NA → str() ได้ '<NA>')"""
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "nat", "none", "<na>") else s


def _build_missing_df(spx_rows) -> pd.DataFrame:
    """
    สร้างตาราง 'Missing in WMS' จาก SPX rows
    รับได้ทั้ง DataFrame และ list ของ Series
    SPX TO No. จะมีค่าเมื่อ source เป็น SPX Transport Order (.xlsx)
    """
    df = (spx_rows if isinstance(spx_rows, pd.DataFrame) else pd.DataFrame(spx_rows))
    df = df.reset_index(drop=True)
    if df.empty:
        return pd.DataFrame(columns=_MISSING_COLS)

    def _col(name: str) -> pd.Series:
        if name in df.columns:
            return df[name].map(_clean_str)
        return pd.Series([""] * len(df), index=df.index)

    return pd.DataFrame({
        "Status":         pd.Series(["Missing in WMS ⚠️"] * len(df), index=df.index),
        "SPX TO No.":     _col("to_number"),
        "Order SN (SPX)": _col("order_sn"),
        "SPX Tracking":   _col("tracking"),
        "Pickup Time":    _col("pickup_time"),
    })


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


# ─── Order SN  /  ออเดอร์ที่มีหลาย Tracking ──────────────────────────────────

# suffix ลำดับกล่องท้าย Order No — "-00", "-01", "-02"
_ORDER_SUFFIX_RE = re.compile(r"-\d+$")
# Shopee Order SN = YYMMDD + ตัวอักษร/ตัวเลข เช่น "2607303NH00FVN"
_SHOPEE_SN_RE = re.compile(r"^\d{6}[A-Za-z0-9]{6,}$")

ORDER_SN_COL     = "Order SN"
ORDER_NTRK_COL   = "# Tracking ในออเดอร์"
ORDER_OTHER_COL  = "Tracking อื่นในออเดอร์"
ORDER_LOADS_COL  = "Load อื่นในออเดอร์"
ORDER_GROUP_COLS = [ORDER_SN_COL, ORDER_NTRK_COL, ORDER_OTHER_COL, ORDER_LOADS_COL]


def extract_order_sn(order_no) -> str:
    """
    ดึง Shopee Order SN ออกจาก Order No — 1 ออเดอร์อาจถูกแยกเป็นหลายกล่อง/หลาย tracking
    โดยใช้ Order SN เดียวกันแต่ต่างที่ suffix ลำดับกล่อง

      "2607302FNDAAFV-00"                 → "2607302FNDAAFV"
      "CMGSHP313088592-2607302U57HVHP-01" → "2607302U57HVHP"

    ถ้า segment สุดท้ายไม่เข้ารูปแบบ Shopee SN (เช่น Mirakl
    "CMGMRL-CDS2607312611453174_2076-A-01") จะคืนทั้งก้อนที่ตัด suffix แล้ว
    เพื่อไม่ให้ออเดอร์ต่างกันถูกยุบรวมเป็นคีย์เดียว
    """
    s = _clean_str(order_no)
    if not s:
        return ""
    stem = _ORDER_SUFFIX_RE.sub("", s)
    last = stem.rsplit("-", 1)[-1]
    return last if _SHOPEE_SN_RE.match(last) else stem


def build_order_groups(order_nos, trackings, loads=None) -> dict[str, dict]:
    """
    จัดกลุ่ม tracking ตาม Order SN

    ควรสร้างจาก FC Export DO เพราะเห็นทั้งคลัง — จึงบอกได้ว่าออเดอร์นี้ยังมีกล่องอื่น
    ที่อยู่นอก Load ที่กำลังตรวจหรือไม่ (TLD เห็นเฉพาะ Load ที่อัปโหลด)

    Returns: {order_sn: {"trackings": [...], "loads": [...]}}
    """
    loads = loads if loads is not None else [None] * len(order_nos)
    groups: dict[str, dict] = {}

    for ono, trk, load in zip(order_nos, trackings, loads):
        sn = extract_order_sn(ono)
        trk = _clean_str(trk)
        if not sn or not trk:
            continue
        g = groups.setdefault(sn, {"trackings": [], "loads": []})
        if trk not in g["trackings"]:
            g["trackings"].append(trk)
        ld = _clean_str(load)
        if ld and ld not in g["loads"]:
            g["loads"].append(ld)

    return groups


def annotate_order_groups(
    df: pd.DataFrame,
    groups: dict[str, dict],
    order_cols: tuple = ("Order No", "เลขที่ Order (FC)"),
    trk_cols: tuple = ("Tracking Number", "SPX Tracking"),
    current_load: str = "",
) -> tuple[pd.DataFrame, dict]:
    """
    เติมคอลัมน์บอกว่าออเดอร์นี้มีกี่ tracking และ tracking อื่นอยู่ที่ไหน

    เพิ่ม: Order SN | # Tracking ในออเดอร์ | Tracking อื่นในออเดอร์ | Load อื่นในออเดอร์

    Returns (df, stats) — stats = {"multi": จำนวนแถวที่ออเดอร์มี >1 tracking,
                                   "cross_load": จำนวนแถวที่กล่องอื่นอยู่คนละ Load}
    """
    empty_stats = {"multi": 0, "cross_load": 0}
    if df.empty:
        return df, empty_stats

    order_cols = tuple(c for c in order_cols if c in df.columns)
    trk_cols   = tuple(c for c in trk_cols   if c in df.columns)
    if not order_cols:
        return df, empty_stats

    cur = _clean_str(current_load)
    sns, counts, others, other_loads = [], [], [], []

    for _, row in df.iterrows():
        sn = ""
        for c in order_cols:
            sn = extract_order_sn(row.get(c, ""))
            if sn:
                break

        own = {_clean_str(row.get(c, "")) for c in trk_cols}
        own.discard("")

        g = groups.get(sn)
        if not g:
            # ไม่มีในกลุ่ม (เช่น ไม่ได้อัปโหลด FC) — นับจากตัวมันเองอย่างน้อย 1
            sns.append(sn); counts.append(1 if own else 0)
            others.append(""); other_loads.append("")
            continue

        rest       = [t for t in g["trackings"] if t not in own]
        rest_loads = [l for l in g["loads"] if l and l != cur]

        sns.append(sn)
        counts.append(len(g["trackings"]))
        others.append(", ".join(rest))
        other_loads.append(", ".join(rest_loads))

    out = df.copy()
    out[ORDER_SN_COL]    = sns
    out[ORDER_NTRK_COL]  = counts
    out[ORDER_OTHER_COL] = others
    out[ORDER_LOADS_COL] = other_loads

    return out, {
        "multi":      int(sum(1 for c in counts if c > 1)),
        "cross_load": int(sum(1 for l in other_loads if l)),
    }


# ─── รายการที่ Match ไม่ได้ + วิเคราะห์สาเหตุ ─────────────────────────────────

SIDE_COL      = "ฝั่ง"
SIDE_SPX_ONLY = "มีใน SPX ไม่มีใน TLD"
SIDE_TLD_ONLY = "มีใน TLD ไม่มีใน SPX"

UNMATCHED_COLS = [SIDE_COL, "Tracking", "Order SN", "Order No (WMS)", "SPX TO No.", "เวลา"]

# คอลัมน์ผลวิเคราะห์การจับคู่
PAIR_COLS = ["Tracking (SPX)", "Tracking (TLD)", "Order SN", "Order No (WMS)", "สาเหตุที่น่าจะเป็น"]

# ถ้าทั้งสองฝั่งใหญ่กว่านี้ ข้ามการเทียบตัวอักษรทีละคู่ (O(n×m)) เพื่อไม่ให้ค้าง
_PAIR_SCAN_LIMIT = 500


def _norm_tracking(s: str) -> str:
    """ตัดช่องว่าง/ขีด แล้วทำเป็นตัวพิมพ์ใหญ่ — ใช้จับ tracking ที่ต่างกันแค่รูปแบบ"""
    return "".join(ch for ch in _clean_str(s).upper() if ch.isalnum())


def _char_diff(a: str, b: str) -> int:
    """จำนวนตำแหน่งที่ต่างกัน (ยาวเท่ากันเท่านั้น) — ใช้จับ tracking ที่พิมพ์/สแกนผิดไม่กี่ตัว"""
    if len(a) != len(b):
        return 999
    return sum(1 for x, y in zip(a, b) if x != y)


def build_unmatched_df(missing_df: pd.DataFrame, extra_df: pd.DataFrame) -> pd.DataFrame:
    """
    รวมรายการที่จับคู่ไม่ได้จากทั้งสองฝั่งเป็นตารางเดียว โครงสร้างเดียวกัน
    เพื่อให้เทียบกันได้ตรงๆ แทนที่จะแยกเป็น "ขาด" กับ "เกิน" ซึ่งอ่านแล้วเข้าใจผิด
    """
    parts = []

    if missing_df is not None and not missing_df.empty:
        m = missing_df.reset_index(drop=True)
        parts.append(pd.DataFrame({
            SIDE_COL:          SIDE_SPX_ONLY,
            "Tracking":        m["SPX Tracking"].map(_clean_str)   if "SPX Tracking"   in m else "",
            "Order SN":        m["Order SN (SPX)"].map(_clean_str) if "Order SN (SPX)" in m else "",
            "Order No (WMS)":  "",
            "SPX TO No.":      m["SPX TO No."].map(_clean_str)     if "SPX TO No."     in m else "",
            "เวลา":            m["Pickup Time"].map(_fmt_dt)       if "Pickup Time"    in m else "",
        }))

    if extra_df is not None and not extra_df.empty:
        e = extra_df.reset_index(drop=True)
        _ono = e["Order No"].map(_clean_str) if "Order No" in e else pd.Series([""] * len(e), index=e.index)
        parts.append(pd.DataFrame({
            SIDE_COL:          SIDE_TLD_ONLY,
            "Tracking":        e["Tracking Number"].map(_clean_str) if "Tracking Number" in e else "",
            "Order SN":        _ono.map(extract_order_sn),
            "Order No (WMS)":  _ono,
            "SPX TO No.":      "",
            "เวลา":            e["Create date&time"].map(_fmt_dt)   if "Create date&time" in e else "",
        }))

    if not parts:
        return pd.DataFrame(columns=UNMATCHED_COLS)
    return pd.concat(parts, ignore_index=True)[UNMATCHED_COLS]


def pair_unmatched(missing_df: pd.DataFrame, extra_df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    หาว่ารายการที่ค้างทั้งสองฝั่ง จริงๆ แล้วเป็นของชิ้นเดียวกันที่ map ไม่ติดหรือไม่

    ถ้า SPX เหลือ 1 และ TLD ก็เหลือ 1 มักไม่ใช่ "ของหาย 1 + ของเกิน 1"
    แต่เป็นชิ้นเดียวกันที่จับคู่ไม่ได้เพราะ Tracking เขียนต่างกัน

    ลำดับการจับคู่: Order SN ตรงกัน → Tracking ต่างแค่รูปแบบ → Tracking ต่างไม่กี่ตัวอักษร

    Returns (pairs_df, stats)
    """
    stats = {"spx_only": 0, "tld_only": 0, "paired": 0, "scan_skipped": False}

    spx = [] if missing_df is None or missing_df.empty else [
        {"trk": _clean_str(r.get("SPX Tracking", "")),
         "sn":  _clean_str(r.get("Order SN (SPX)", ""))}
        for _, r in missing_df.iterrows()
    ]
    tld = [] if extra_df is None or extra_df.empty else [
        {"trk": _clean_str(r.get("Tracking Number", "")),
         "ono": _clean_str(r.get("Order No", "")),
         "sn":  extract_order_sn(r.get("Order No", ""))}
        for _, r in extra_df.iterrows()
    ]
    stats["spx_only"], stats["tld_only"] = len(spx), len(tld)

    if not spx or not tld:
        return pd.DataFrame(columns=PAIR_COLS), stats

    used_tld: set[int] = set()
    pairs = []

    def _take(i_spx: int, j_tld: int, reason: str) -> None:
        used_tld.add(j_tld)
        s, t = spx[i_spx], tld[j_tld]
        pairs.append({
            "Tracking (SPX)":     s["trk"],
            "Tracking (TLD)":     t["trk"],
            "Order SN":           s["sn"] or t["sn"],
            "Order No (WMS)":     t["ono"],
            "สาเหตุที่น่าจะเป็น": reason,
        })

    matched_spx: set[int] = set()

    # 1) Order SN ตรงกัน — สัญญาณแรงสุด
    sn_map: dict[str, int] = {}
    for j, t in enumerate(tld):
        if t["sn"]:
            sn_map.setdefault(t["sn"], j)
    for i, s in enumerate(spx):
        j = sn_map.get(s["sn"]) if s["sn"] else None
        if j is not None and j not in used_tld:
            _take(i, j, "Order SN ตรงกัน แต่ Tracking ไม่ตรง")
            matched_spx.add(i)

    # 2) Tracking ต่างแค่รูปแบบ (ตัวพิมพ์ / ช่องว่าง / ขีด)
    norm_map: dict[str, int] = {}
    for j, t in enumerate(tld):
        if j not in used_tld and t["trk"]:
            norm_map.setdefault(_norm_tracking(t["trk"]), j)
    for i, s in enumerate(spx):
        if i in matched_spx or not s["trk"]:
            continue
        j = norm_map.get(_norm_tracking(s["trk"]))
        if j is not None and j not in used_tld:
            _take(i, j, "Tracking เหมือนกัน ต่างแค่รูปแบบ (ตัวพิมพ์/ช่องว่าง)")
            matched_spx.add(i)

    # 3) Tracking ต่างไม่กี่ตัวอักษร — ข้ามถ้าข้อมูลใหญ่เกินไป
    rest_spx = [i for i in range(len(spx)) if i not in matched_spx and spx[i]["trk"]]
    rest_tld = [j for j in range(len(tld)) if j not in used_tld and tld[j]["trk"]]
    if len(rest_spx) > _PAIR_SCAN_LIMIT or len(rest_tld) > _PAIR_SCAN_LIMIT:
        stats["scan_skipped"] = True
    else:
        for i in rest_spx:
            a = _norm_tracking(spx[i]["trk"])
            best, best_d = None, 3           # ยอมรับต่างได้ไม่เกิน 2 ตัว
            for j in rest_tld:
                if j in used_tld:
                    continue
                d = _char_diff(a, _norm_tracking(tld[j]["trk"]))
                if d < best_d:
                    best, best_d = j, d
            if best is not None:
                _take(i, best, f"Tracking ต่างกัน {best_d} ตัวอักษร — อาจสแกน/คีย์ผิด")
                matched_spx.add(i)

    stats["paired"] = len(pairs)
    return pd.DataFrame(pairs, columns=PAIR_COLS), stats


def collect_multi_tracking(dfs) -> tuple[pd.DataFrame, int]:
    """
    รวมแถวจากหลายตารางที่ออเดอร์มีมากกว่า 1 tracking

    Returns (df, n_cross_load) — n_cross_load = จำนวนแถวที่กล่องอื่นอยู่คนละ Load
    คืน DataFrame ว่างเมื่อไม่พบ (ไม่ใช่ raise — pd.concat([]) จะ error)
    """
    parts = []
    for df in dfs:
        if df is None or df.empty or ORDER_NTRK_COL not in df.columns:
            continue
        hit = df[pd.to_numeric(df[ORDER_NTRK_COL], errors="coerce").fillna(0) > 1]
        if not hit.empty:
            parts.append(hit)

    if not parts:
        return pd.DataFrame(), 0

    out = pd.concat(parts, ignore_index=True)
    n_cross = int(out[ORDER_LOADS_COL].map(_clean_str).ne("").sum()) if ORDER_LOADS_COL in out.columns else 0
    return out, n_cross


# ─── Enrichment จาก FC Export DO ─────────────────────────────────────────────

FC_FOUND_COL = "พบใน FC"


def _fc_candidate_keys(row, trk_cols: tuple, order_cols: tuple) -> list[str]:
    """
    รายการคีย์ที่จะลองค้นใน FC index ตามลำดับความสำคัญ:
      1. Tracking Number  2. Order No เต็ม  3. segment ของ Order No (≥6 ตัวอักษร)
    """
    keys = []
    for c in trk_cols:
        v = _clean_str(row.get(c, "")) if c else ""
        if v:
            keys.append(v)
    for c in order_cols:
        v = _clean_str(row.get(c, "")) if c else ""
        if not v:
            continue
        keys.append(v)
        keys.extend(p.strip() for p in v.split("-") if len(p.strip()) >= 6)
    return keys


def enrich_with_fc(
    df: pd.DataFrame,
    fc_df: pd.DataFrame,
    fc_index: dict[str, int],
    labels: list[str],
    trk_cols: tuple = ("Tracking Number", "SPX Tracking"),
    order_cols: tuple = ("Order No", "SPX Order SN", "Order SN (SPX)"),
) -> tuple[pd.DataFrame, int]:
    """
    เติมคอลัมน์จาก FC Export DO เข้า df — join ด้วย Tracking Number ก่อน
    แล้ว fallback เป็น Order No / segment

    Returns (df ที่เติมแล้ว, จำนวนแถวที่หา FC เจอ)
    """
    if df.empty or fc_df.empty or not labels:
        return df, 0

    trk_cols   = tuple(c for c in trk_cols   if c in df.columns)
    order_cols = tuple(c for c in order_cols if c in df.columns)
    if not trk_cols and not order_cols:
        return df, 0

    positions = []
    for _, row in df.iterrows():
        pos = -1
        for k in _fc_candidate_keys(row, trk_cols, order_cols):
            if k in fc_index:
                pos = fc_index[k]
                break
        positions.append(pos)

    out = df.copy()
    n_found = sum(1 for p in positions if p >= 0)
    out[FC_FOUND_COL] = ["✅" if p >= 0 else "—" for p in positions]
    for label in labels:
        if label not in fc_df.columns:
            continue
        col = fc_df[label]
        out[label] = [col.iat[p] if p >= 0 else "" for p in positions]

    return out, n_found


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
        "Tracking Number", "Brand No", "Pallet No", "Carton No",
        "Create date&time", "Arrival date&time", "Handover date&time",
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
            spx_merge_rows.append({tld_order_col: ono, "SPX TO No.": _clean_str(r.get("to_number", "")),
                                    "SPX Tracking": trk, "SPX Order SN": str(r["order_sn"]).strip(),
                                    "_pickup_raw": str(r.get("pickup_time", "") or "").strip()})
    for r in matched_by_orderkey:
        osn = str(r["order_sn"]).strip()
        ono = tld_orderkey_to_order_no.get(osn, "")
        if ono:
            spx_merge_rows.append({tld_order_col: ono, "SPX TO No.": _clean_str(r.get("to_number", "")),
                                    "SPX Tracking": str(r["tracking"]).strip(), "SPX Order SN": osn,
                                    "_pickup_raw": str(r.get("pickup_time", "") or "").strip()})

    spx_merge_df = (
        pd.DataFrame(spx_merge_rows)
        if spx_merge_rows
        else pd.DataFrame(columns=[tld_order_col, "SPX TO No.", "SPX Tracking", "SPX Order SN", "_pickup_raw"])
    )
    if not matched_tld.empty:
        matched_df = matched_tld.merge(spx_merge_df, on=tld_order_col, how="left")
        matched_df.insert(0, "Match Method",
                          matched_df[tld_order_col].apply(
                              lambda o: "Tracking" if o in tracking_order_nos else "Order SN"
                          ))
        matched_df.insert(0, "Status", "Matched ✅")
        matched_df.insert(1, "TLD No.", tld_no)
        # Compute duration BEFORE formatting (need raw datetime strings)
        if "Create date&time" in matched_df.columns and "_pickup_raw" in matched_df.columns:
            matched_df["ระยะเวลา SPX-WMS (ชม.)"] = matched_df.apply(
                lambda row: _delta_hours(row["Create date&time"], row["_pickup_raw"]), axis=1
            )
        # Format datetimes
        for _dtc in ("Create date&time", "Arrival date&time", "Handover date&time"):
            if _dtc in matched_df.columns:
                matched_df[_dtc] = matched_df[_dtc].apply(_fmt_dt)
        if "_pickup_raw" in matched_df.columns:
            matched_df["SPX Pickup Time"] = matched_df["_pickup_raw"].apply(_fmt_dt)
            matched_df.drop(columns=["_pickup_raw"], inplace=True)
    else:
        matched_df = pd.DataFrame()

    # ── Missing in WMS ────────────────────────────────────────────────────────
    miss_wms_df = _build_missing_df(missing_rows)

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