"""Tính MCDX, các danh sách và chỉ số hỗ trợ quyết định trên bảng giá dạng rộng (ngày × mã)."""
from __future__ import annotations

import numpy as np
import pandas as pd

# ---- Tham số (chỉnh tại đây) -------------------------------------------------
BANKER_P, BANKER_BASE, BANKER_SENS = 50, 50, 1.5
HOT_P, HOT_BASE, HOT_SENS = 40, 30, 0.7
BANKER_MA = 10
MIN_VALUE20 = 5e9            # GTGD TB20 tối thiểu (đồng)
SUSTAIN_MIN_STREAK = 5
SUSTAIN_MAX_GAIN = 25.0      # % tăng tối đa trong chuỗi
HOT_EXT_MA20 = 10.0          # % trên MA20 coi là tăng nóng
TOP_N = 10
INDEX = "VNINDEX"


def rsi_wilder(close: pd.DataFrame, p: int) -> pd.DataFrame:
    d = close.diff()
    gain, loss = d.clip(lower=0), -d.clip(upper=0)
    ag = gain.ewm(alpha=1 / p, min_periods=p, adjust=False).mean()
    al = loss.ewm(alpha=1 / p, min_periods=p, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).where(al.notna()).fillna(100).where(al.notna())


def wide(prices: pd.DataFrame, col: str, dates: pd.Index) -> pd.DataFrame:
    w = prices.pivot_table(index="date", columns="symbol", values=col, aggfunc="last", dropna=False)
    return w.reindex(dates)


def build_frames(prices: pd.DataFrame, symbols: list[str]):
    px = prices[prices["symbol"].isin(symbols + [INDEX])].copy()
    num = ["open", "high", "low", "close", "volume", "value", "fnet"]
    px[num] = px[num].apply(pd.to_numeric, errors="coerce")
    idx_dates = px.loc[px["symbol"] == INDEX, "date"]
    dates = pd.Index(sorted(idx_dates.unique() if len(idx_dates) > 100 else px["date"].unique()))
    F = {c: wide(px, c, dates) for c in ["open", "high", "low", "close", "volume", "value", "fnet"]}
    for c in ["open", "high", "low", "close"]:
        F[c] = F[c].ffill()
    F["volume"] = F["volume"].fillna(0)
    return dates, F


def compute(prices: pd.DataFrame, universe: dict[str, list[str]], sectors: dict[str, str], holdings: list[str]) -> dict:
    basket_of = {s: b for b, syms in universe.items() for s in syms}
    symbols = sorted(basket_of)
    dates, F = build_frames(prices, symbols)
    symbols = [s for s in symbols if s in F["close"].columns and F["close"][s].notna().sum() >= 60]
    C = F["close"][symbols]
    H, Lo, V = F["high"][symbols], F["low"][symbols], F["volume"][symbols]
    val = F["value"][symbols].where(F["value"][symbols].notna(), C * V)
    fnet = F["fnet"][symbols]

    banker = (BANKER_SENS * (rsi_wilder(C, BANKER_P) - BANKER_BASE)).clip(0, 20)
    hot = (HOT_SENS * (rsi_wilder(C, HOT_P) - HOT_BASE)).clip(0, 20)
    bma = banker.fillna(0).rolling(BANKER_MA).mean()
    ma20, ma50 = C.rolling(20).mean(), C.rolling(50).mean()
    ma200 = C.rolling(200, min_periods=150).mean()
    vma20 = V.rolling(20).mean()
    up_vr = (V / vma20).where(C > C.shift())  # KL/TB20 của các phiên tăng giá
    value20 = val.rolling(20).mean()
    tr = pd.concat([H - Lo, (H - C.shift()).abs(), (Lo - C.shift()).abs()]).groupby(level=0).max()
    atr14 = tr.ewm(alpha=1 / 14, adjust=False).mean()
    hi250 = H.rolling(250, min_periods=60).max()
    hi20, lo20, hi60 = H.rolling(20).max(), Lo.rolling(20).min(), H.rolling(60).max()

    b1, b5 = banker.shift(1), banker.shift(5)
    m1 = bma.shift(1)
    new_in = (banker > 0.5) & (b5 <= 0.5)
    cross = (b1 <= m1) & (banker > bma) & (banker > 0)
    streak = _streak(banker >= 10)
    liquid = value20 >= MIN_VALUE20

    # ---- chỉ số thị trường
    has_idx = INDEX in F["close"].columns and F["close"][INDEX].notna().sum() > 60
    idx = F["close"][INDEX] if has_idx else (C / C.iloc[0]).mean(axis=1) * 1000
    breadth = (banker > 0).sum(axis=1) / banker.notna().sum(axis=1).replace(0, np.nan) * 100
    above20 = (C > ma20).sum(axis=1) / C.notna().sum(axis=1).replace(0, np.nan) * 100
    breadth_b = {b: (banker[[s for s in symbols if basket_of[s] == b]] > 0).mean(axis=1) * 100 for b in universe}

    t = len(dates) - 1
    I = idx.iloc[t]
    ima50, ima200 = idx.rolling(50).mean().iloc[t], idx.rolling(200, min_periods=120).mean().iloc[t]
    br, ab = float(breadth.iloc[t]), float(above20.iloc[t])
    if (pd.notna(ima200) and I < ima200) or br < 25:
        state = "defense"
    elif I > ima50 and (pd.isna(ima200) or I > ima200) and br >= 50 and ab >= 60:
        state = "attack"
    else:
        state = "caution"
    REG = {"attack": ("Tấn công", "70–100%"), "caution": ("Thận trọng", "30–50%"), "defense": ("Phòng thủ", "dưới 20%")}

    # ---- RS rating
    idx_r20, idx_r60 = idx.iloc[t] / idx.iloc[t - 20] - 1, idx.iloc[t] / idx.iloc[t - 60] - 1
    r20, r60 = C.iloc[t] / C.iloc[t - 20] - 1, C.iloc[t] / C.iloc[t - 60] - 1
    rs_raw = 0.5 * (r20 - idx_r20) + 0.5 * (r60 - idx_r60)
    rs_rating = (rs_raw.rank(pct=True) * 98 + 1).round()

    # ---- ngành
    sec_of = {s: sectors.get(s, "Khác") for s in symbols}
    sec_df = banker.T.groupby(pd.Series(sec_of)).mean().T  # ngày × ngành
    sec_cnt = (banker > 0).T.groupby(pd.Series(sec_of)).sum().T
    sec_now = sec_df.iloc[t]

    def last(s, col=None):
        x = s.iloc[t] if col is None else s[col].iloc[t]
        return None if pd.isna(x) else float(x)

    rows, checks = {}, {}
    market_ok = state != "defense"
    for s in symbols:
        c = C[s].iloc[t]
        vr = V[s].iloc[t] / vma20[s].iloc[t] if vma20[s].iloc[t] else np.nan
        f5 = fnet[s].iloc[t - 4:t + 1].sum(min_count=1)
        d5 = banker[s].iloc[t] - b5[s].iloc[t]
        chg = (c / C[s].iloc[t - 1] - 1) * 100
        score = 60 * np.clip(d5, 0, 20) / 20 + 25 * min(np.nan_to_num(vr), 3) / 3 + (15 if (pd.notna(f5) and f5 > 0) else 0)
        k = int(streak[s].iloc[t])
        avg_b = float(banker[s].iloc[t - k + 1:t + 1].mean()) if k else 0.0
        gain = (c / C[s].iloc[t - k] - 1) * 100 if k and t - k >= 0 else 0.0
        ext = (c / ma20[s].iloc[t] - 1) * 100 if pd.notna(ma20[s].iloc[t]) else np.nan
        atr = atr14[s].iloc[t]
        stop_c = [c - 2 * atr] + ([ma20[s].iloc[t]] if ma20[s].iloc[t] < c else [])
        stop = max(stop_c)
        res60, res250 = hi60[s].iloc[t], hi250[s].iloc[t]
        target = res60 if res60 > c * 1.03 else (res250 if pd.notna(res250) and res250 > c * 1.03 else c + 3 * atr)
        rr = (target - c) / (c - stop) if c > stop else np.nan
        rows[s] = dict(
            sym=s, basket=basket_of[s], sector=sec_of[s], price=c, chg=chg,
            b=banker[s].iloc[t], b1=b1[s].iloc[t], d5=d5, ma=bma[s].iloc[t], hot=hot[s].iloc[t],
            vr=vr, f5=None if pd.isna(f5) else float(f5) / 1e9, value20=value20[s].iloc[t] / 1e9,
            liquid=bool(liquid[s].iloc[t]), new_in=bool(new_in[s].iloc[t]), cross=bool(cross[s].iloc[t]),
            score=score, streak=k, streak_avg=avg_b, streak_gain=gain, ext=ext,
            above_ma20=bool(c > ma20[s].iloc[t]), rs=float(rs_rating.get(s, np.nan)),
            atr=atr, stop=stop, target=target, rr=rr,
            near_high=c / hi250[s].iloc[t] if pd.notna(hi250[s].iloc[t]) else np.nan,
            tight=(hi20[s].iloc[t] - lo20[s].iloc[t]) / c,
        )
        # ---- 10 tiêu chí thời điểm (docs/tieu-chi-co-phieu-tot.md, vế 2)
        m50, m200 = ma50[s].iloc[t], ma200[s].iloc[t]
        m200_prev = ma200[s].iloc[t - 20] if t >= 20 else np.nan
        trend_ok = None if pd.isna(m200) else bool(c > m50 and c > m200 and pd.notna(m200_prev) and m200 > m200_prev)
        near = rows[s]["near_high"]
        vmax5 = up_vr[s].iloc[t - 4:t + 1].max()
        rsv = rows[s]["rs"]
        ck = [
            ("market", market_ok, state),
            ("trend", trend_ok, None if pd.isna(m200) else (c / m200 - 1) * 100),
            ("rs", None if pd.isna(rsv) else bool(rsv >= 80), rsv),
            ("near_high", None if pd.isna(near) else bool(near >= 0.85), None if pd.isna(near) else (near - 1) * 100),
            ("base", bool(rows[s]["tight"] <= 0.15), rows[s]["tight"] * 100),
            ("banker", bool(banker[s].iloc[t] > 0 and banker[s].iloc[t] >= bma[s].iloc[t]), banker[s].iloc[t]),
            ("volume", None if pd.isna(vmax5) else bool(vmax5 >= 1.5), None if pd.isna(vmax5) else vmax5),
            ("foreign", None if pd.isna(f5) else bool(f5 > 0), None if pd.isna(f5) else float(f5) / 1e9),
            ("not_hot", None if pd.isna(ext) else bool(ext <= HOT_EXT_MA20), ext),
            ("liquidity", bool(liquid[s].iloc[t]), value20[s].iloc[t] / 1e9),
        ]
        checks[s] = [{"k": k, "ok": ok, "v": None if v is None or (isinstance(v, float) and np.isnan(v)) else (v if isinstance(v, str) else float(v))} for k, ok, v in ck]
        rows[s]["timing"] = sum(1 for _, ok, _ in ck if ok)

    R = pd.DataFrame(rows).T
    num = R.columns.difference(["sym", "basket", "sector", "liquid", "new_in", "cross", "above_ma20"])
    R[num] = R[num].astype(float)

    def is_new(r):
        return r.liquid and (r.new_in or r.cross)

    new_lists = {b: R[[is_new(r) and r.basket == b for r in R.itertuples()]].sort_values("score", ascending=False) for b in universe}
    sus = R[(R.liquid) & (R.streak >= SUSTAIN_MIN_STREAK) & (R.b >= R.ma) & (R.above_ma20) & (R.streak_gain <= SUSTAIN_MAX_GAIN)]
    sus = sus.assign(sus_score=sus.streak * sus.streak_avg).sort_values("sus_score", ascending=False)

    # ---- Top 5 tín hiệu mạnh nhất
    cand = R[R.sym.isin(set().union(*[set(d.sym) for d in new_lists.values()], set(sus.sym)))].copy()
    if len(cand):
        mc = np.maximum(cand.score / 100, np.minimum(cand.streak, 20) / 20 * cand.streak_avg / 20)
        rs_p = cand.rs.fillna(50) / 99
        sec_p = cand.sector.map(sec_now).fillna(0) / 20
        vol_p = np.where(cand.chg > 0, np.clip((cand.vr.fillna(0) - 1) / 0.5, 0, 1), 0)
        base_p = 0.5 * np.clip((cand.near_high.fillna(0) - 0.85) / 0.15, 0, 1) + 0.5 * np.clip((0.15 - cand.tight) / 0.10, 0, 1)
        parts = pd.DataFrame({"p_mcdx": 30 * mc, "p_rs": 25 * rs_p, "p_sector": 15 * np.clip(sec_p, 0, 1),
                              "p_volume": 15 * vol_p, "p_base": 15 * base_p}, index=cand.index)
        cand["total"] = parts.sum(axis=1)
        cand = cand.join(parts)
        # ưu tiên mã chưa tăng nóng (giá cách MA20 ≤ 10%), mã tăng nóng xếp sau
        cand["hot_ext"] = cand.ext > HOT_EXT_MA20
        top5 = cand.sort_values(["hot_ext", "total"], ascending=[True, False]).head(5).drop(columns="hot_ext")
    else:
        top5 = cand

    # ---- cảnh báo thoát cho mã đang giữ
    exits = []
    for s in holdings:
        if s not in R.index:
            exits.append({"sym": s, "status": "unknown", "reason": "Không có trong dữ liệu (ngoài VN30/VNMidcap hoặc chưa tải)"})
            continue
        r = R.loc[s]
        reasons = []
        if r.b1 > 0.5 and r.b <= 0.5:
            reasons.append("Banker về 0")
        if r.b < r.ma and banker[s].iloc[t - 1] >= bma[s].iloc[t - 1]:
            reasons.append("Banker cắt xuống MA10")
        if r.price < r.stop:
            reasons.append("Giá thủng mức cắt lỗ gợi ý")
        status = "exit" if reasons else ("watch" if r.b < r.ma else "hold")
        exits.append({"sym": s, "status": status, "reason": "; ".join(reasons) or ("Banker dưới MA10" if status == "watch" else "Banker còn trên MA10"),
                      "b": r.b, "ma": r.ma, "price": r.price, "stop": r.stop})

    # ---- ngành dẫn dắt
    leaders = (sec_cnt.iloc[t] - sec_cnt.iloc[t - 5]).sort_values(ascending=False)
    leaders = [{"sector": k, "d5": int(v), "now": int(sec_cnt.iloc[t][k])} for k, v in leaders.head(3).items()]

    # ---- hiệu quả tín hiệu (kiểm chứng trên lịch sử)
    perf = []
    sig_new = (new_in | cross) & liquid
    sig_sus = (streak >= SUSTAIN_MIN_STREAK) & (banker >= bma) & (C > ma20) & liquid
    for name, sig in [("Dòng tiền mới vào", sig_new), ("Duy trì dòng tiền lớn", sig_sus)]:
        rec = {"name": name}
        for h in (5, 10, 20):
            fwd = C.shift(-h) / C - 1
            v = fwd.where(sig.fillna(False)).iloc[60:-h].to_numpy().ravel()
            vals = pd.Series(v[~np.isnan(v)])
            rec[f"n{h}"] = int(len(vals))
            rec[f"win{h}"] = float((vals > 0).mean() * 100) if len(vals) else None
            rec[f"ret{h}"] = float(vals.mean() * 100) if len(vals) else None
        idx_fwd = {str(h): float(((idx.shift(-h) / idx - 1).iloc[60:-h]).mean() * 100) for h in (5, 10, 20)}
        rec["idx"] = idx_fwd
        perf.append(rec)

    # ---- chuỗi cho biểu đồ (60 phiên)
    W = 60
    tail = slice(max(0, t - W + 1), t + 1)
    ds = [d.isoformat() for d in dates[tail]]

    def arr(df, s, nd=2, scale=1.0):
        return [None if pd.isna(x) else round(float(x) / scale, nd) for x in df[s].iloc[tail]]

    stocks = {}
    for s in symbols:
        stocks[s] = {"close": arr(C, s), "volume": arr(V, s, 0), "vma20": arr(vma20, s, 0),
                     "fnet": arr(fnet, s, 2, 1e9), "banker": arr(banker, s, 1), "hot": arr(hot, s, 1),
                     "bma": arr(bma, s, 1)}

    def rec(r):
        d = {k: (None if (isinstance(v, float) and np.isnan(v)) else (round(v, 3) if isinstance(v, float) else v)) for k, v in r.to_dict().items()}
        return d

    return {
        "date": dates[t].isoformat(),
        "regime": {"state": state, "label": REG[state][0], "alloc": REG[state][1], "index": float(I),
                   "index_chg": float((I / idx.iloc[t - 1] - 1) * 100),
                   "ma50": None if pd.isna(ima50) else float(ima50), "ma200": None if pd.isna(ima200) else float(ima200),
                   "breadth": br, "breadth5": float(breadth.iloc[t - 5]), "above20": ab, "has_index": bool(has_idx)},
        "series": {"dates": ds, "index": [round(float(x), 2) for x in idx.iloc[tail]],
                   "breadth": {b: [round(float(x), 1) for x in breadth_b[b].iloc[tail]] for b in universe}},
        "stocks": stocks,
        "rows": {s: rec(R.loc[s]) for s in symbols},
        "lists": {"new": {b: list(new_lists[b].sym) for b in universe},
                  "sustain": list(sus.sym),
                  "top5": [{**rec(r), "parts": {k: round(float(r["p_" + k]), 1) for k in ["mcdx", "rs", "sector", "volume", "base"]}} for _, r in top5.iterrows()],
                  "out": list(R[R.liquid & (((b5.iloc[t].reindex(R.index) >= 5) & (R.d5 <= -5)) | ((R.b1 > 0.5) & (R.b <= 0.5)))].sym)},
        "exits": exits,
        "checks": checks,
        "sectors": {"dates": [d.isoformat() for d in dates[max(0, t - 9):t + 1]],
                    "rows": sorted([{"sector": k, "vals": [round(float(x), 1) for x in sec_df[k].iloc[max(0, t - 9):t + 1]]} for k in sec_df.columns],
                                   key=lambda r: -r["vals"][-1])},
        "leaders": leaders,
        "perf": perf,
        "universe_size": {b: len([s for s in symbols if basket_of[s] == b]) for b in universe},
    }


def _streak(cond: pd.DataFrame) -> pd.DataFrame:
    out = np.zeros(cond.shape, dtype=int)
    a = cond.fillna(False).to_numpy()
    for i in range(len(a)):
        out[i] = np.where(a[i], (out[i - 1] if i else 0) + 1, 0)
    return pd.DataFrame(out, index=cond.index, columns=cond.columns)
