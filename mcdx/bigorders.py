"""Lệnh khớp lớn trong phiên (mặc định ≥ 80.000 cổ) từ dữ liệu khớp lệnh: TCBS iFlash Open API khi có token
(mỗi dòng là một lần khớp riêng), nếu không thì vnstock (KBS, các phần khớp của một lệnh chủ động gộp thành một dòng).

Mỗi giao dịch có chiều chủ động: "buy" = bên mua chủ động khớp vào giá bán (mua chủ động),
"sell" = bên bán chủ động khớp vào giá mua (bán chủ động). Khớp lệnh định kỳ mở cửa/đóng cửa (ATO/ATC)
là tổng của nhiều lệnh nên không tính là lệnh lớn. Một lệnh đặt lớn có thể khớp thành nhiều giao dịch nhỏ,
nên đây là "giao dịch khớp lớn", không phải toàn bộ lệnh đặt lớn.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import time

import pandas as pd

from . import store

log = logging.getLogger("bigorders")
THRESHOLD = int(os.environ.get("BIG_THRESHOLD", "80000") or 80000)
MAX_TRADES = 40       # số giao dịch lớn giữ lại cho mỗi mã (lớn nhất)
KEEP_DAYS = 10        # số phiên lưu lại trong data/bigorders/
OUT = store.DATA / "bigorders"


def priority(prices: pd.DataFrame, symbols: list[str], first: list[str]) -> list[str]:
    """Thứ tự quét: mã ưu tiên (đang nắm giữ, đang có tín hiệu) rồi đến mã có GTGD 20 phiên lớn nhất."""
    df = prices[prices["symbol"].isin(symbols)].sort_values("date")
    v20 = df.groupby("symbol").tail(20).groupby("symbol")["value"].mean() if "value" in df else pd.Series(dtype=float)
    rest = sorted(symbols, key=lambda s: -float(v20.get(s, 0) or 0))
    return list(dict.fromkeys([s for s in first if s in symbols] + rest))


def summarize(sym: str, ticks: pd.DataFrame, threshold: int = THRESHOLD) -> dict | None:
    """ticks: time (datetime), price (đồng), volume (cổ), side ('buy'|'sell'|'ato'|'atc'|...)."""
    if ticks is None or len(ticks) == 0:
        return None
    total = float(ticks["volume"].sum())
    big = ticks[(ticks["volume"] >= threshold) & ticks["side"].isin(["buy", "sell"])].copy()
    if big.empty:
        return {"sym": sym, "total_vol": total, "buy_n": 0, "sell_n": 0, "buy_vol": 0, "sell_vol": 0,
                "buy_val": 0.0, "sell_val": 0.0, "net_vol": 0, "net_val": 0.0, "share": 0.0, "trades": []}
    big["val"] = big["price"] * big["volume"]
    b, s = big[big.side == "buy"], big[big.side == "sell"]
    out = {"sym": sym, "total_vol": total,
           "buy_n": int(len(b)), "sell_n": int(len(s)),
           "buy_vol": int(b.volume.sum()), "sell_vol": int(s.volume.sum()),
           "buy_val": round(float(b.val.sum()) / 1e9, 2), "sell_val": round(float(s.val.sum()) / 1e9, 2)}
    out["net_vol"] = out["buy_vol"] - out["sell_vol"]
    out["net_val"] = round(out["buy_val"] - out["sell_val"], 2)
    out["share"] = round((out["buy_vol"] + out["sell_vol"]) / total * 100, 1) if total else 0.0
    top = big.nlargest(MAX_TRADES, "volume").sort_values("time")
    out["trades"] = [{"t": r.time.strftime("%H:%M:%S"), "p": float(r.price), "v": int(r.volume), "side": r.side}
                     for r in top.itertuples()]
    return out


def scan(client, symbols: list[str], minutes: float, session: dt.date, threshold: int = THRESHOLD,
         workers: int = 1, source: str | None = None) -> dict:
    """Quét các mã trong giới hạn thời gian (song song khi workers > 1). Chỉ nhận dữ liệu của đúng phiên `session`.
    client.ticks_df(sym) (nếu có) hoặc client.ticks(sym) trả DataFrame time/price/volume/side."""
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.monotonic()
    deadline = t0 + minutes * 60 if minutes else None
    fetch = getattr(client, "ticks_df", None) or client.ticks
    state = {"errors": 0, "ok": 0, "stop": False}

    def one(s):
        if state["stop"] or (deadline and time.monotonic() > deadline):
            return s, "skip", None
        try:
            tk = fetch(s)
            state["ok"] += 1
            return s, "ok", tk
        except Exception as e:
            state["errors"] += 1
            log.warning("Không lấy được khớp lệnh %s: %s", s, e)
            if state["errors"] >= 10 and state["ok"] == 0:
                state["stop"] = True
            return s, "err", None

    by_sym, done = {}, []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for s, st, tk in ex.map(one, symbols):
            if st != "ok":
                continue
            done.append(s)
            if tk is None or len(tk) == 0 or tk["time"].dt.date.max() != session:
                continue
            r = summarize(s, tk[tk["time"].dt.date == session], threshold)
            if r and (r["buy_n"] or r["sell_n"]):
                by_sym[s] = r
    if state["stop"]:
        log.warning("Nguồn khớp lệnh lỗi liên tục, dừng quét.")
    scanned = len(done)
    res = {"date": session.isoformat(), "threshold": threshold, "scanned": scanned, "total": len(symbols),
           "updated": dt.datetime.now().strftime("%H:%M"), "scanned_syms": done, "by_sym": by_sym,
           "source": source or getattr(client, "tick_source", "KBS")}
    log.info("Lệnh lớn ≥ %s cổ: quét %d/%d mã, %d mã có lệnh lớn (%.0f giây)", f"{threshold:,}", scanned,
             len(symbols), len(by_sym), time.monotonic() - t0)
    OUT.mkdir(parents=True, exist_ok=True)
    store.write_json(OUT / "latest.json", res)
    store.write_json(OUT / f"{session.isoformat()}.json", res)
    for old in sorted(OUT.glob("20*.json"))[:-KEEP_DAYS]:  # chỉ giữ KEEP_DAYS phiên gần nhất
        old.unlink()
    return res


def load_latest() -> dict:
    return store.read_json(OUT / "latest.json", {})


def load_hist(n: int = 5, upto: str | None = None) -> list[dict]:
    """n phiên gần nhất đã quét (mới nhất trước), không sau ngày `upto`."""
    files = sorted(OUT.glob("20*.json"), reverse=True)
    out = []
    for f in files:
        if upto and f.stem > upto:
            continue
        d = store.read_json(f, {})
        if d.get("date"):
            out.append(d)
        if len(out) >= n:
            break
    return out


def symbol_history(hist: list[dict], sym: str) -> list[dict]:
    """Lệnh lớn của một mã theo từng phiên. scanned=False khi phiên đó chưa quét tới mã."""
    rows = []
    for d in hist:
        r = (d.get("by_sym") or {}).get(sym)
        if r:
            rows.append({"date": d["date"], "scanned": True, "src": d.get("source", "KBS"), **{k: v for k, v in r.items() if k != "sym"}})
        elif sym in set(d.get("scanned_syms") or []):
            rows.append({"date": d["date"], "scanned": True, "src": d.get("source", "KBS"), "buy_n": 0, "sell_n": 0, "buy_vol": 0, "sell_vol": 0,
                         "buy_val": 0.0, "sell_val": 0.0, "net_vol": 0, "net_val": 0.0, "share": 0.0, "trades": []})
        else:
            rows.append({"date": d["date"], "scanned": False})
    return rows
