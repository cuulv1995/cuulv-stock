"""Gọi thẳng API công khai của KB Securities (KBS), thay cho vnstock 4.x (ngừng hoạt động từ 10/11/2026).

Cung cấp các lớp Quote / Listing / Trading với đúng những phương thức và cột dữ liệu mà VnstockClient dùng,
để phần còn lại của pipeline không phải thay đổi:

    Quote(sym).history(start, end, interval="1D")   → time, open, high, low, close, volume (cổ phiếu: nghìn đồng)
    Quote(sym).intraday(page_size, page)            → time, price, volume, match_type (buy/sell/other)
    Listing().symbols_by_group(group)               → danh sách mã của rổ (VN30, VNMidCap, VNSmallCap, ...)
    Listing().symbols_by_exchange()                 → symbol, organ_name
    Listing().symbols_by_industries(lang="vi")      → symbol, industry_code, industry_name
    Trading().price_board(symbols_list, get_all)    → bảng giá phiên hiện tại (open_price, close_price, ...)

    python -m mcdx.kbs_shim            # thử nhanh từng endpoint (in kích thước dữ liệu trả về)
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sys
import time

import pandas as pd
import requests

log = logging.getLogger("kbs")

IIS = "https://kbbuddywts.kbsec.com.vn/iis-server/investment"
INDEXES = {"VNINDEX", "HNXINDEX", "UPCOMINDEX", "VN30", "HNX30", "VN100"}
GROUP_CODE = {"VN30": "30", "VN100": "100", "VNMidCap": "MID", "VNSmallCap": "SML", "HOSE": "HOSE", "HNX": "HNX",
              "UPCOM": "UPCOM", "VNALL": "ALL", "VNXALL": "XALL"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "x-lang": "vi",
}
BOARD_MAP = {"SB": "symbol", "OP": "open_price", "HI": "high_price", "LO": "low_price", "CP": "close_price",
             "TT": "volume_accumulated", "TV": "total_value", "FB": "foreign_buy_volume", "FS": "foreign_sell_volume",
             "RE": "reference_price", "EX": "exchange"}

_session = requests.Session()
_session.headers.update(HEADERS)


def _req(method: str, url: str, timeout: int = 30, **kw):
    r = _session.request(method, url, timeout=timeout, **kw)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"KBS HTTP {r.status_code} {url.split('/investment')[-1].split('?')[0]}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"KBS trả dữ liệu không phải JSON: {r.text[:200]}") from None


def _items(j, *keys):
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        for k in keys:
            if isinstance(j.get(k), list):
                return j[k]
    return []


def _sym_of(x) -> str:
    if isinstance(x, str):
        return x.strip().upper()
    if isinstance(x, dict):
        for k in ("symbol", "sb", "SB", "code", "stockCode", "ticker"):
            if x.get(k):
                return str(x[k]).strip().upper()
    return ""


class Quote:
    def __init__(self, symbol: str, **_):
        self.symbol = symbol.upper()

    def history(self, start: str, end: str, interval: str = "1D", **_) -> pd.DataFrame:
        fmt = lambda s: dt.date.fromisoformat(str(s)[:10]).strftime("%d-%m-%Y")  # noqa: E731
        kind = "index" if self.symbol in INDEXES else "stocks"
        j = _req("GET", f"{IIS}/{kind}/{self.symbol}/data_day", params={"sdate": fmt(start), "edate": fmt(end)})
        rows = j.get("data_day") if isinstance(j, dict) else None
        if not rows:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rows).rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df["time"] = pd.to_datetime(df["time"])
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df[["time", "open", "high", "low", "close", "volume"]].sort_values("time").reset_index(drop=True)

    def intraday(self, page_size: int = 100, page: int = 1, **_) -> pd.DataFrame:
        j = _req("GET", f"{IIS}/trade/history/{self.symbol}", params={"page": page, "limit": page_size})
        rows = _items(j, "data")
        if not rows:
            return pd.DataFrame(columns=["time", "price", "volume", "match_type"])
        df = pd.DataFrame(rows)

        def ts(r):
            t = r.get("t")
            if isinstance(t, str) and t.count(":") >= 3:  # "2026-01-14 14:27:23:15" (mili giây sau dấu ':')
                return t.rsplit(":", 1)[0]
            if isinstance(t, str) and len(t) > 8:
                return t
            d = r.get("TD")
            if isinstance(d, str) and "/" in d:
                d = dt.datetime.strptime(d, "%d/%m/%Y").date().isoformat()
            return f"{d} {r.get('FT')}"
        side = {"B": "buy", "S": "sell"}
        return pd.DataFrame({
            "time": pd.to_datetime([ts(r) for r in rows]),
            "price": pd.to_numeric(df.get("FMP"), errors="coerce"),
            "volume": pd.to_numeric(df.get("FV"), errors="coerce"),
            "match_type": [side.get(str(r.get("LC") or "").upper(), str(r.get("LC") or "other").lower() or "other")
                           for r in rows],
        })


class Listing:
    def __init__(self, **_):
        pass

    def symbols_by_group(self, group: str = "VN30", **_) -> pd.Series:
        code = GROUP_CODE.get(group, group)
        j = _req("GET", f"{IIS}/index/{code}/stocks")
        syms = [s for s in (_sym_of(x) for x in _items(j, "data", "stocks")) if s]
        return pd.Series(sorted(set(syms)), name="symbol")

    def symbols_by_exchange(self, **_) -> pd.DataFrame:
        j = _req("GET", f"{IIS}/stock/search/data")
        rows = _items(j, "data")
        out = [{"symbol": _sym_of(r), "organ_name": r.get("name") or r.get("organName") or ""}
               for r in rows if isinstance(r, dict) and str(r.get("type", "stock")).lower() == "stock"]
        return pd.DataFrame(out, columns=["symbol", "organ_name"])

    def symbols_by_industries(self, lang: str = "vi", **_) -> pd.DataFrame:
        inds = _items(_req("GET", f"{IIS}/sector/all"), "data")
        out = []
        for ind in inds:
            code, name = ind.get("code"), ind.get("name")
            if code is None:
                continue
            try:
                j = _req("GET", f"{IIS}/sector/stock", params={"code": code, "l": 1})
            except Exception as e:
                log.warning("Không lấy được mã ngành %s: %s", name, e)
                continue
            for x in _items(j, "stocks", "data"):
                s = _sym_of(x)
                if s:
                    out.append({"symbol": s, "industry_code": code, "industry_name": name})
            time.sleep(0.2)
        return pd.DataFrame(out, columns=["symbol", "industry_code", "industry_name"])


class Trading:
    def __init__(self, symbol: str | None = None, **_):
        pass

    def price_board(self, symbols_list: list[str], get_all: bool = False, **_) -> pd.DataFrame:
        j = _req("POST", f"{IIS}/stock/iss", data=json.dumps({"code": ",".join(symbols_list)}),
                 headers={"Content-Type": "application/json"})
        rows = _items(j, "data")
        if not rows:
            return pd.DataFrame(columns=list(BOARD_MAP.values()))
        return pd.DataFrame(rows).rename(columns=BOARD_MAP)


def _probe():
    logging.basicConfig(level=logging.INFO)
    today = dt.date.today()
    checks = {
        "history FPT": lambda: Quote("FPT").history((today - dt.timedelta(days=10)).isoformat(), today.isoformat()),
        "history VNINDEX": lambda: Quote("VNINDEX").history((today - dt.timedelta(days=10)).isoformat(), today.isoformat()),
        "intraday VIX": lambda: Quote("VIX").intraday(page_size=50000, page=1),
        "group VN30": lambda: Listing().symbols_by_group("VN30"),
        "group VNMidCap": lambda: Listing().symbols_by_group("VNMidCap"),
        "group VNSmallCap": lambda: Listing().symbols_by_group("VNSmallCap"),
        "names": lambda: Listing().symbols_by_exchange(),
        "board": lambda: Trading().price_board(["FPT", "VIX", "HPG"], get_all=True),
    }
    ok = True
    for name, fn in checks.items():
        try:
            t0 = time.time()
            r = fn()
            n = len(r)
            print(f"OK   {name}: {n} dòng ({time.time() - t0:.1f}s)")
            if isinstance(r, pd.DataFrame) and n:
                print("     ", r.head(2).to_dict("records"), "| cột:", list(r.columns)[:12])
            elif n:
                print("     ", list(r)[:8])
        except Exception as e:
            ok = False
            print(f"LỖI {name}: {e}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    _probe()
