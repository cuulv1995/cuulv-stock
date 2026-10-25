"""Báo cáo phân tích từng mã: cơ bản, kỹ thuật, dòng tiền, điểm mạnh/yếu, kết luận và kế hoạch giao dịch.

Đầu vào là chuỗi giá đầy đủ của mã (pandas Series), dòng thông số từ analysis.compute, báo cáo tài chính
đã lưu đệm (fundamentals.json) và trạng thái thị trường. Kết quả là dict gọn, ghi thành site/analysis/<MÃ>.json.
Mọi ngưỡng là quy tắc phổ biến để tham khảo, không phải khuyến nghị đầu tư.
"""
from __future__ import annotations

import math
import statistics

import numpy as np
import pandas as pd

BANK_SECTORS = {"Ngân hàng"}
FIN_SECTORS = {"Ngân hàng", "Chứng khoán", "Bảo hiểm", "Tài chính khác"}


# ---------------------------------------------------------------- tiện ích
def _f(x, nd=2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, nd)


def _pct(a, b):
    a, b = _f(a, 6), _f(b, 6)
    if a is None or b is None or b == 0:
        return None
    if b < 0 and a < 0:
        return None  # lỗ sang lỗ: tỷ lệ không có ý nghĩa
    return round((a / b - 1) * 100 * (1 if b > 0 else -1), 1)


def _vn(x, nd=1):
    """Số kiểu Việt Nam: 1.234,5"""
    if x is None:
        return "–"
    s = f"{x:,.{nd}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _ty(x):
    """Đồng → tỷ đồng."""
    return None if x is None else round(x / 1e9, 1)


def _k(x):
    """Giá đồng → nghìn đồng (chuỗi)."""
    return _vn(None if x is None else x / 1000, 2)


def _series_vals(rec: dict | None, key: str) -> list[tuple[str, float]]:
    d = (rec or {}).get(key) or {}
    return [(k, v) for k, v in d.items() if v is not None]


def _latest(rec: dict | None, key: str):
    vals = _series_vals(rec, key)
    return vals[-1][1] if vals else None


# ---------------------------------------------------------------- kỹ thuật
def rsi(c: pd.Series, p: int = 14) -> float | None:
    d = c.diff()
    ag = d.clip(lower=0).ewm(alpha=1 / p, adjust=False).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1 / p, adjust=False).mean()
    v = 100 - 100 / (1 + ag.iloc[-1] / al.iloc[-1]) if al.iloc[-1] else 100.0
    return _f(v, 1)


def macd(c: pd.Series):
    m = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    s = m.ewm(span=9, adjust=False).mean()
    h = m - s
    cross = None
    for k in range(1, 6):  # cắt trong 5 phiên gần nhất
        if len(h) > k and h.iloc[-k] > 0 >= h.iloc[-k - 1]:
            cross = ("up", k - 1)
            break
        if len(h) > k and h.iloc[-k] < 0 <= h.iloc[-k - 1]:
            cross = ("down", k - 1)
            break
    return _f(m.iloc[-1], 2), _f(s.iloc[-1], 2), _f(h.iloc[-1], 2), cross


def pivots(h: pd.Series, lo: pd.Series, win: int = 3, look: int = 120):
    """Đỉnh/đáy cục bộ (cao/thấp nhất trong ±win phiên) của `look` phiên gần nhất."""
    h, lo = h.iloc[-look:], lo.iloc[-look:]
    highs, lows = [], []
    for i in range(win, len(h) - win):
        if h.iloc[i] == h.iloc[i - win:i + win + 1].max():
            highs.append(float(h.iloc[i]))
        if lo.iloc[i] == lo.iloc[i - win:i + win + 1].min():
            lows.append(float(lo.iloc[i]))
    return highs, lows


def _dedupe(levels: list[float], tol=0.02) -> list[float]:
    out: list[float] = []
    for v in levels:
        if all(abs(v / x - 1) > tol for x in out):
            out.append(v)
    return out


def technical(C: pd.Series, H: pd.Series, L: pd.Series, V: pd.Series) -> dict:
    C, H, L, V = C.dropna(), H.dropna(), L.dropna(), V.fillna(0)
    c = float(C.iloc[-1])
    ma = {n: (_f(C.rolling(n, min_periods=int(n * .75)).mean().iloc[-1], 0) if len(C) >= n * .75 else None) for n in (20, 50, 200)}
    ma200_prev = C.rolling(200, min_periods=150).mean().iloc[-21] if len(C) > 170 else None
    tr = pd.concat([H - L, (H - C.shift()).abs(), (L - C.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
    hi52, lo52 = float(H.iloc[-250:].max()), float(L.iloc[-250:].min())
    hi20 = float(H.iloc[-21:-1].max()) if len(H) > 21 else float(H.max())
    m, s, hst, cross = macd(C)
    r = rsi(C)
    ph, pl = pivots(H, L)
    sup = sorted(_dedupe(sorted([x for x in pl if x < c * 0.995], reverse=True)), reverse=True)[:3]
    res = sorted(_dedupe(sorted([x for x in ph if x > c * 1.005])))[:3]
    if hi52 > c * 1.005 and all(abs(hi52 / x - 1) > 0.02 for x in res):
        res = sorted(res + [hi52])[:3]
    v20 = V.iloc[-20:].mean()
    perf = {k: _pct(c, C.iloc[-1 - n]) if len(C) > n else None for k, n in (("w1", 5), ("m1", 21), ("m3", 63), ("m6", 126))}
    a20, a50, a200 = ma[20], ma[50], ma[200]
    if a20 and a50 and a200 and c > a20 > a50 > a200:
        trend, tone = "Tăng mạnh: giá > MA20 > MA50 > MA200", "good"
    elif a50 and a200 and c > a50 and c > a200:
        trend, tone = "Tăng: giá trên MA50 và MA200", "good"
    elif a200 and c > a200:
        trend, tone = "Đi ngang trên MA200, chưa vượt MA50" if a50 and c < a50 else "Hồi phục trên MA200", "warn"
    elif a200 and a20 and a50 and c > a20 and c > a50:
        trend, tone = f"Hồi phục: trên MA20, MA50, còn dưới MA200 {_vn(abs(_pct(c, a200) or 0))}%", "warn"
    elif a200:
        trend, tone = "Giảm: giá dưới MA200", "bad"
    else:
        trend, tone = "Chưa đủ dữ liệu MA200", "warn"
    return {
        "price": c, "ma20": a20, "ma50": a50, "ma200": a200,
        "ma200_up": None if ma200_prev is None or a200 is None else bool(a200 > ma200_prev),
        "rsi": r, "macd": {"v": m, "s": s, "h": hst, "cross": cross},
        "atr": _f(atr, 0), "atr_pct": _f(atr / c * 100, 1), "hi52": hi52, "lo52": lo52, "hi20": hi20,
        "from_hi52": _pct(c, hi52), "from_lo52": _pct(c, lo52),
        "supports": [round(x) for x in sup], "resistances": [round(x) for x in res],
        "vol_ratio": _f(V.iloc[-1] / v20, 2) if v20 else None,
        "vol5_20": _f(V.iloc[-5:].mean() / v20, 2) if v20 else None,
        "perf": perf, "trend": trend, "trend_tone": tone,
    }


# ---------------------------------------------------------------- cơ bản
def _ratio_latest_prev(rat: dict, key: str):
    """Giá trị kỳ mới nhất và trung bình các kỳ trước (bỏ cột trùng có hậu tố _n)."""
    d = {k: v for k, v in ((rat or {}).get(key) or {}).items() if v is not None and "_" not in k}
    if not d:
        return None, None
    ks = list(d)
    prev = [d[k] for k in ks[:-1]]
    return d[ks[-1]], (statistics.mean(prev) if prev else None)


def fundamentals(rec: dict | None, price: float, sector: str, peers: dict) -> dict:
    """Đánh giá cơ bản dựa trên bảng chỉ số tài chính KBS (nhất quán trong một lần trả về)."""
    if not rec or not (rec.get("ratio") or rec.get("income")):
        return {"has": False}
    inc, rat = rec.get("income") or {}, rec.get("ratio") or {}
    bank = sector in BANK_SECTORS
    fin = sector in FIN_SECTORS
    L = lambda k: _f(_latest(rat, k), 2)  # noqa: E731

    # 4 quý gần nhất (tham khảo)
    q_rev = dict(_series_vals({"x": inc.get("revenue")}, "x"))
    q_np = dict(_series_vals({"x": inc.get("net_profit") or inc.get("net_profit_total")}, "x"))
    q_gp = dict(_series_vals({"x": inc.get("gross_profit")}, "x"))
    periods = sorted(set(q_rev) | set(q_np))
    quarters = [{"p": p, "rev": _ty(q_rev.get(p)), "np": _ty(q_np.get(p)),
                 "gm": _f(q_gp[p] / q_rev[p] * 100, 1) if q_gp.get(p) and q_rev.get(p) else None} for p in periods]

    eps, bvps = L("eps_ttm"), L("bvps")
    pe_now = _f(price / eps, 1) if eps and eps > 0 else None
    pb_now = _f(price / bvps, 2) if bvps and bvps > 0 else None
    roe = _f(L("roe"), 1)
    np_yoy = _f(L("np_growth"), 1)
    rev_yoy = _f(L("nii_growth") if bank else L("rev_growth"), 1)
    eq_g = _f(L("equity_growth"), 1)
    gm_q = [q["gm"] for q in quarters if q["gm"] is not None]
    gm_now = gm_q[-1] if gm_q else _f(L("gross_margin"), 1)
    gm_prev = _f(statistics.mean(gm_q[:-1]), 1) if len(gm_q) >= 2 else None
    debt_assets, eq_assets = _f(L("debt_assets"), 1), _f(L("equity_assets"), 1)
    cur, ldr, cir = _f(L("current_ratio"), 2), _f(L("ldr"), 1), _f(L("cir"), 1)
    div, icov = _f(L("div_yield"), 2), _f(L("interest_cover"), 1)
    g_peg = min(np_yoy, 50) if np_yoy and np_yoy > 0 else None  # tăng trưởng đột biến làm PEG mất ý nghĩa → giới hạn 50%
    peg = _f(pe_now / g_peg, 2) if pe_now and g_peg else None
    pe_med = peers.get("pe")

    crit = []

    def add(key, label, ok, val, th):
        crit.append({"k": key, "label": label, "ok": ok, "val": val, "th": th})
    pct = lambda v: None if v is None else f"{'+' if v > 0 else ''}{_vn(v)}%"  # noqa: E731
    add("np_yoy", "Lợi nhuận sau thuế quý gần nhất so với cùng kỳ", None if np_yoy is None else np_yoy >= 20, pct(np_yoy), "≥ 20%")
    add("rev_yoy", "Thu nhập lãi thuần so với cùng kỳ" if bank else "Doanh thu thuần so với cùng kỳ",
        None if rev_yoy is None else rev_yoy >= 15, pct(rev_yoy), "≥ 15%")
    add("eq_g", "Vốn chủ sở hữu tăng trưởng", None if eq_g is None else eq_g >= 10, pct(eq_g), "≥ 10%")
    add("roe", "ROE bình quân 4 quý", None if roe is None else roe >= 15, None if roe is None else f"{_vn(roe)}%", "≥ 15%")
    if bank:
        add("cir", "Chi phí hoạt động / thu nhập (CIR)", None if cir is None else cir <= 40,
            None if cir is None else f"{_vn(cir)}%", "≤ 40%")
        add("capital", "Vốn chủ sở hữu / tổng tài sản", None if eq_assets is None else eq_assets >= 8,
            None if eq_assets is None else f"{_vn(eq_assets)}%", "≥ 8%")
        add("ldr", "Cho vay / huy động (LDR)", None if ldr is None else ldr <= 85, None if ldr is None else f"{_vn(ldr)}%", "≤ 85%")
    else:
        add("gm", "Biên lợi nhuận gộp ổn định hoặc tăng", None if gm_now is None or gm_prev is None else gm_now >= gm_prev - 1,
            None if gm_now is None else f"{_vn(gm_now)}%" + (f" (các quý trước TB {_vn(gm_prev)}%)" if gm_prev is not None else ""),
            "không giảm quá 1 điểm %")
        if fin:
            add("debt", "Nợ vay / tổng tài sản", None, None if debt_assets is None else f"{_vn(debt_assets)}%", "không áp dụng cho công ty tài chính")
            add("liq", "Thanh toán hiện hành", None, None if cur is None else _vn(cur, 2), "không áp dụng cho công ty tài chính")
        else:
            add("debt", "Nợ vay / tổng tài sản", None if debt_assets is None else debt_assets <= 30,
                None if debt_assets is None else f"{_vn(debt_assets)}%", "≤ 30%")
            add("liq", "Thanh toán hiện hành (tài sản / nợ ngắn hạn)", None if cur is None else cur >= 1,
                None if cur is None else f"{_vn(cur, 2)} lần", "≥ 1 lần")
    pe_th = pe_med * 1.2 if pe_med else 15
    add("pe", "P/E so với ngành", None if pe_now is None else pe_now <= pe_th,
        None if pe_now is None else _vn(pe_now) + (f" (trung vị ngành {_vn(pe_med)})" if pe_med else ""),
        f"≤ {_vn(pe_th)}" + (" (1,2 × ngành)" if pe_med else ""))
    add("peg", "PEG = P/E ÷ tăng trưởng lợi nhuận", None if peg is None else peg < 1,
        None if peg is None else _vn(peg, 2) + (" (tăng trưởng tính tối đa 50%)" if np_yoy and np_yoy > 50 else ""), "< 1")
    q_ok = None
    if div is not None:
        q_ok = bool(div > 0 and (fin or icov is None or icov >= 3))
    add("quality", "Trả cổ tức tiền mặt" + ("" if fin else ", đủ khả năng trả lãi vay"), q_ok,
        ", ".join(x for x in [f"tỷ suất cổ tức {_vn(div, 2)}%" if div is not None else None,
                              f"trả lãi gấp {_vn(icov)} lần" if icov is not None and not fin else None] if x) or None,
        "cổ tức > 0" + ("" if fin else "; trả lãi ≥ 3 lần"))
    evaluable = [c for c in crit if c["ok"] is not None]
    score = round(sum(c["ok"] for c in evaluable) / len(evaluable) * 10) if len(evaluable) >= 4 else None
    ratios = {k: L(k) for k in ("pe", "pb", "ps", "roe", "roa", "gross_margin", "net_margin", "current_ratio", "debt_assets",
                                "liab_assets", "equity_assets", "div_yield", "beta", "nim", "cir", "ldr", "loan_growth",
                                "deposit_growth", "eps_ttm", "bvps", "interest_cover")}
    return {
        "has": True, "updated": rec.get("updated"), "bank": bank, "fin": fin, "quarters": quarters[-4:],
        "rev_yoy": rev_yoy, "np_yoy": np_yoy, "eq_growth": eq_g, "pe_now": pe_now, "pb_now": pb_now, "peg": peg, "roe": roe,
        "peers": peers, "ratios": ratios, "criteria": crit, "score": score, "n_eval": len(evaluable),
    }


def sector_peers(fund_cache: dict, sectors: dict, prices: dict) -> dict:
    """Trung vị P/E, P/B, ROE theo ngành (dùng giá hiện tại)."""
    by: dict = {}
    for s, rec in fund_cache.items():
        rat = (rec or {}).get("ratio") or {}
        eps, bvps, roe = _latest(rat, "eps_ttm"), _latest(rat, "bvps"), _latest(rat, "roe")
        p = prices.get(s)
        sec = sectors.get(s, "Khác")
        d = by.setdefault(sec, {"pe": [], "pb": [], "roe": []})
        if p and eps and eps > 0 and 0 < p / eps < 80:
            d["pe"].append(p / eps)
        if p and bvps and bvps > 0:
            d["pb"].append(p / bvps)
        if roe is not None:
            d["roe"].append(roe)
    out = {}
    for sec, d in by.items():
        out[sec] = {k: (_f(statistics.median(v), 2) if len(v) >= 3 else None) for k, v in d.items()}
        out[sec]["n"] = max(len(v) for v in d.values()) if d else 0
    return out


# ---------------------------------------------------------------- kế hoạch giao dịch
def trade_plan(t: dict, row: dict, regime: str) -> dict:
    c, atr = t["price"], t["atr"] or t["price"] * 0.02
    ma20, ma50 = t["ma20"], t["ma50"]
    sup = t["supports"]
    near_sup = next((x for x in sup if x >= c * 0.88), None)
    # Vùng mua hợp lý: nhịp điều chỉnh về MA20 / hỗ trợ gần
    if ma20 and c > ma20:
        lo = ma20
        if near_sup and ma20 < near_sup < c:
            lo = max(ma20, near_sup)
        hi = min(c, lo * 1.03)
    else:
        lo = near_sup or c * 0.96
        hi = c
    if hi <= lo:
        hi = lo * 1.02
    mid = (lo + hi) / 2
    # Điểm mua tấn công: vượt kháng cự gần nhất (đỉnh 20 phiên) kèm khối lượng
    res = [x for x in t["resistances"] if x <= c * 1.15]
    hi20 = t["hi20"]
    breakout_now = c > hi20 and (row.get("vr") or 0) >= 1.5
    if breakout_now:
        attack = {"price": round(c), "level": round(hi20), "active": True,
                  "cond": f"Đang vượt đỉnh 20 phiên {_k(hi20)} với KL {_vn(row.get('vr'), 2)}× TB20"}
    else:
        lvl = min([x for x in res if x > c] + ([hi20] if hi20 > c else []), default=None)
        attack = None if lvl is None else {"price": round(lvl * 1.005), "level": round(lvl), "active": False,
                                           "cond": f"Mua khi giá vượt {_k(lvl)} kèm khối lượng ≥ 1,5× TB20"}
    # Cắt lỗ: dưới vùng mua 1 ATR, tối đa 8% so với giữa vùng
    stop = max(lo - atr, mid * 0.92)
    below = [x for x in sup if x < lo]
    if below and below[0] > stop * 0.97 and below[0] < lo:
        stop = min(stop, below[0] * 0.99)
    risk = mid - stop
    # Mục tiêu
    ups = sorted([x for x in t["resistances"] if x > max(c, mid) * 1.03])
    t1 = ups[0] if ups else mid + 2 * risk
    if t1 < mid + 1.5 * risk:
        t1 = mid + 2 * risk
    t2 = next((x for x in ups if x > t1 * 1.03), None) or max(t["hi52"], mid + 3 * risk)
    if t2 <= t1 * 1.02:
        t2 = mid + 3 * risk
    rr = (t1 - mid) / risk if risk > 0 else None
    alloc = {"attack": "Tối đa 10–15% danh mục cho mã này; chia 2 lần: 50% ở vùng mua, 50% khi kích hoạt điểm tấn công",
             "caution": "Tối đa 5–10% danh mục; ưu tiên mua ở vùng mua hợp lý, chỉ tấn công khi khối lượng xác nhận",
             "defense": "Thị trường Phòng thủ: đứng ngoài hoặc mua thăm dò ≤ 5% danh mục"}[regime]
    return {"zone": [round(lo), round(hi)], "attack": attack, "stop": round(stop), "stop_pct": _f((stop / mid - 1) * 100, 1),
            "t1": round(t1), "t1_pct": _f((t1 / mid - 1) * 100, 1), "t2": round(t2), "t2_pct": _f((t2 / mid - 1) * 100, 1),
            "rr": _f(rr, 1), "alloc": alloc, "in_zone": bool(lo <= c <= hi * 1.005)}


# ---------------------------------------------------------------- tổng hợp
def build(sym: str, frames: dict, row: dict, checks: list, fund_rec: dict | None, peers: dict,
          regime: dict, name: str | None, flow_extra: dict) -> dict:
    t = technical(frames["close"], frames["high"], frames["low"], frames["volume"])
    f = fundamentals(fund_rec, t["price"], row["sector"], peers)
    timing = int(row.get("timing") or 0)
    fs = f.get("score") if f.get("has") else None
    rg = regime["state"]
    plan = trade_plan(t, row, rg)
    strengths, weaknesses = [], []

    # --- cơ bản
    if f.get("has"):
        for cr in f["criteria"]:
            if cr["ok"] is True:
                strengths.append(f"{cr['label']}: {cr['val']}")
            elif cr["ok"] is False:
                weaknesses.append(f"{cr['label']}: {cr['val']} (cần {cr['th']})")
        if f.get("pb_now") and peers.get("pb") and f["pb_now"] < peers["pb"] * 0.8:
            strengths.append(f"P/B {_vn(f['pb_now'], 2)} thấp hơn trung vị ngành {_vn(peers['pb'], 2)}")
    # --- kỹ thuật
    if t["trend_tone"] == "good":
        strengths.append(f"Xu hướng {t['trend'].split(':')[0].lower()}" + (": " + t["trend"].split(":", 1)[1].strip() if ":" in t["trend"] else ""))
    elif t["trend_tone"] == "bad":
        weaknesses.append(t["trend"])
    if t["rsi"] is not None:
        if t["rsi"] >= 75:
            weaknesses.append(f"RSI {_vn(t['rsi'])}: quá mua, dễ điều chỉnh ngắn hạn")
        elif t["rsi"] <= 30:
            weaknesses.append(f"RSI {_vn(t['rsi'])}: quá bán, lực bán còn mạnh")
    mc = t["macd"]["cross"]
    if mc and mc[0] == "up":
        strengths.append("MACD vừa cắt lên đường tín hiệu" + (" hôm nay" if mc[1] == 0 else f" {mc[1]} phiên trước"))
    elif mc and mc[0] == "down":
        weaknesses.append("MACD vừa cắt xuống đường tín hiệu")
    if t["from_hi52"] is not None and t["from_hi52"] >= -10:
        strengths.append(f"Giá cách đỉnh 52 tuần {_vn(abs(t['from_hi52']))}%: vùng giá mạnh")
    if row.get("ext") is not None and row["ext"] > 10:
        weaknesses.append(f"Giá cao hơn MA20 {_vn(row['ext'])}%: tăng nóng, không nên mua đuổi")
    # --- dòng tiền
    b, bma, streak = row.get("b") or 0, row.get("ma") or 0, int(row.get("streak") or 0)
    if row.get("new_in"):
        strengths.append(f"Dòng tiền lớn vừa vào: Banker từ 0 lên {_vn(b)}")
    elif streak >= 3:
        strengths.append(f"Dòng tiền lớn duy trì {streak} phiên, Banker {_vn(b)} trên MA10")
    if b <= 0.5:
        weaknesses.append("Chưa có dòng tiền lớn (Banker = 0)")
    elif b < bma:
        weaknesses.append(f"Banker {_vn(b)} dưới MA10 {_vn(bma)}: dòng tiền lớn suy yếu")
    if (row.get("rs") or 0) >= 80:
        strengths.append(f"Sức mạnh giá RS {int(row['rs'])}: mạnh hơn {int(row['rs'])}% số mã")
    elif row.get("rs") is not None and row["rs"] < 30:
        weaknesses.append(f"Sức mạnh giá RS {int(row['rs'])}: yếu hơn đa số thị trường")
    f5 = flow_extra.get("f5")
    if f5 is not None:
        (strengths if f5 > 0 else weaknesses).append(f"Khối ngoại {'mua' if f5 > 0 else 'bán'} ròng 5 phiên {_vn(abs(f5))} tỷ")
    if rg == "defense":
        weaknesses.append("Thị trường đang Phòng thủ: rủi ro chung cao")

    # --- kết luận
    hot = (row.get("ext") or 0) > 10
    below200 = t["ma200"] is not None and t["price"] < t["ma200"]
    if below200 and b <= 0.5 and (fs is None or fs <= 4):
        label, tone = "Tránh", "bad"
        why = "Giá dưới MA200, không có dòng tiền lớn" + ("" if fs is None else f", cơ bản yếu ({fs}/10)")
    elif timing >= 7 and (fs is None or fs >= 6):
        if hot:
            label, tone = "Chờ điều chỉnh để mua", "warn"
            why = f"Tín hiệu tốt ({timing}/10) nhưng giá đã tăng nóng; chờ về vùng mua {_k(plan['zone'][0])}–{_k(plan['zone'][1])}"
        else:
            label, tone = "Đáng mua", "good"
            why = f"Thời điểm {timing}/10" + ("" if fs is None else f", cơ bản {fs}/10") + ", dòng tiền và xu hướng ủng hộ"
    elif timing >= 5 and (fs is None or fs >= 5):
        label, tone = "Theo dõi, chờ điểm mua", "warn"
        why = f"Thời điểm {timing}/10" + ("" if fs is None else f", cơ bản {fs}/10") + "; cần thêm xác nhận dòng tiền hoặc vượt kháng cự"
    else:
        label, tone = "Chưa nên mua", "bad"
        why = f"Thời điểm {timing}/10" + ("" if fs is None else f", cơ bản {fs}/10")
    if rg == "defense" and tone == "good":
        label, tone = "Mua thăm dò (thị trường Phòng thủ)", "warn"
        why += "; thị trường chung đang Phòng thủ nên chỉ giải ngân tỷ trọng nhỏ"

    return {
        "sym": sym, "name": name, "sector": row["sector"], "basket": row["basket"], "price": t["price"], "chg": row.get("chg"),
        "verdict": {"label": label, "tone": tone, "why": why},
        "scores": {"timing": timing, "fund": fs, "fund_n": f.get("n_eval")},
        "fund": f, "tech": t,
        "flow": {"banker": _f(b, 1), "bma": _f(bma, 1), "hot": _f(row.get("hot"), 1), "streak": streak, "d5": _f(row.get("d5"), 1),
                 "new_in": bool(row.get("new_in")), "cross": bool(row.get("cross")), "rs": row.get("rs"),
                 "vr": _f(row.get("vr"), 2), "f5": _f(f5, 1), "f20": _f(flow_extra.get("f20"), 1),
                 "value20": _f(row.get("value20"), 1)},
        "strengths": strengths[:8], "weaknesses": weaknesses[:8], "plan": plan, "regime": regime["label"],
    }


# ---------------------------------------------------------------- doanh nghiệp tốt để nắm giữ
def quality(a: dict, liquid: bool) -> dict | None:
    """Điểm doanh nghiệp 0–100 cho nhà đầu tư nắm giữ dài hạn: chất lượng lợi nhuận, tăng trưởng, định giá,
    cổ tức và xu hướng giá dài hạn. Chỉ xét mã đã có BCTC, đủ thanh khoản và vượt các ngưỡng tối thiểu."""
    f, t = a.get("fund") or {}, a.get("tech") or {}
    if not f.get("has") or not liquid:
        return None
    fs, n, roe, g, pe = f.get("score"), f.get("n_eval") or 0, f.get("roe"), f.get("np_yoy"), f.get("pe_now")
    if fs is None or n < 6 or fs < 6 or roe is None or roe < 12 or g is None or g < -10 or not pe or pe <= 0:
        return None
    qn = [q.get("np") for q in f.get("quarters") or [] if q.get("np") is not None]
    if any(x <= 0 for x in qn):
        return None  # có quý lỗ trong 4 quý gần nhất: chưa đủ ổn định để nắm giữ dài hạn
    spike = g > 150  # tăng trưởng đột biến thường do nền so sánh thấp hoặc lãi bất thường
    pe_med = (f.get("peers") or {}).get("pe")
    div = (f.get("ratios") or {}).get("div_yield")
    clamp = lambda x: max(0.0, min(1.0, x))  # noqa: E731
    p_fund = fs * 3
    p_roe = clamp(roe / 30) * 20
    p_g = 8 if spike else clamp(g / 40) * 15
    p_val = (clamp((1.3 - pe / pe_med) / 0.8) if pe_med else clamp((20 - pe) / 12)) * 15
    p_val += 5 if f.get("peg") is not None and f["peg"] < 1 else 0
    if spike:
        p_val *= 0.5  # EPS 4 quý bị đẩy lên bởi lãi đột biến nên P/E thấp chưa phản ánh đúng định giá
    p_div = 5 if div and div >= 3 else 3 if div and div > 0 else 0
    above200 = t.get("ma200") is not None and t["price"] > t["ma200"]
    p_tr = (7 if above200 else 0) + (3 if t.get("ma200_up") else 0)
    q = round(p_fund + p_roe + p_g + p_val + p_div + p_tr)
    why = [f"ROE {_vn(roe)}%", "LNST tăng đột biến (nền thấp)" if spike else f"LNST {'+' if g > 0 else ''}{_vn(g)}%",
           f"P/E {_vn(pe)}" + (f" (ngành {_vn(pe_med)})" if pe_med else "")]
    if div:
        why.append(f"cổ tức {_vn(div, 1)}%")
    if not above200 and t.get("ma200"):
        why.append("giá dưới MA200")
    return {"sym": a["sym"], "q": q, "fs": fs, "roe": roe, "np": g, "pe": pe, "pe_med": pe_med, "div": div,
            "zone": a["plan"]["zone"], "in_zone": a["plan"]["in_zone"], "verdict": a["verdict"]["label"],
            "tone": a["verdict"]["tone"], "above200": above200, "spike": spike, "why": ", ".join(why),
            "parts": {"fund": round(p_fund), "roe": round(p_roe), "growth": round(p_g), "value": round(p_val),
                      "div": p_div, "trend": p_tr}}
