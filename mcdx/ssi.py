"""Client tối giản cho SSI FastConnect Data (REST).

Tài liệu: https://guide.ssi.com.vn/ssi-products/tieng-viet/fastconnect-data/danh-sach-cac-api
Key (ConsumerID / ConsumerSecret) đọc từ biến môi trường, KHÔNG ghi vào code.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

import requests

log = logging.getLogger("ssi")

# Tài liệu SSI ghi 2 dạng đường dẫn; client thử lần lượt và nhớ dạng chạy được.
BASE_URLS = [
    "https://fc-data.ssi.com.vn/v2.0/Market",
    "https://fc-data.ssi.com.vn/api/v2/Market",
]


class SSIError(RuntimeError):
    pass


def fmt_date(d: dt.date) -> str:
    return d.strftime("%d/%m/%Y")


def to_float(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def parse_date(v) -> dt.date | None:
    if not v:
        return None
    s = str(v).strip()[:10]
    for f in ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


class SSIClient:
    bulk_is_cheap = True  # DailyStockPrice trả cả sàn trong 1 lần gọi

    def __init__(self, consumer_id: str, consumer_secret: str, pause: float = 0.35, timeout: int = 30):
        if not consumer_id or not consumer_secret:
            raise SSIError("Thiếu SSI_CONSUMER_ID / SSI_CONSUMER_SECRET (đặt trong GitHub Secrets).")
        self.cid, self.secret = consumer_id, consumer_secret
        self.pause, self.timeout = pause, timeout
        self.http = requests.Session()
        self.base = None
        self.token = None
        self._param_styles = ["lookup", "plain"]

    # ------------------------------------------------------------------ auth
    def authenticate(self):
        errors = []
        for base in BASE_URLS:
            try:
                r = self.http.post(f"{base}/AccessToken",
                                   json={"consumerID": self.cid, "consumerSecret": self.secret},
                                   timeout=self.timeout)
                j = r.json()
                tok = (j.get("data") or {}).get("accessToken")
                if tok:
                    self.base, self.token = base, tok
                    log.info("Đã lấy token SSI qua %s", base)
                    return
                errors.append(f"{base}: {j.get('message') or r.status_code}")
            except Exception as e:  # mạng / JSON lỗi
                errors.append(f"{base}: {e}")
        raise SSIError("Không lấy được token SSI. " + " | ".join(errors))

    # ------------------------------------------------------------------ core GET
    def _get(self, path: str, params: dict, retries: int = 3) -> dict:
        if not self.token:
            self.authenticate()
        last = None
        for attempt in range(retries):
            for style in list(self._param_styles):
                p = {f"lookupRequest.{k}": v for k, v in params.items()} if style == "lookup" else params
                try:
                    r = self.http.get(f"{self.base}/{path}", params=p, timeout=self.timeout,
                                      headers={"Authorization": f"Bearer {self.token}"})
                except requests.RequestException as e:
                    last = str(e)
                    continue
                if r.status_code == 401:
                    self.authenticate()
                    last = "401 hết hạn token"
                    continue
                try:
                    j = r.json()
                except ValueError:
                    last = f"HTTP {r.status_code}, không phải JSON"
                    continue
                data = j.get("data")
                ok = str(j.get("status")) in ("200", "Success", "success") or r.status_code == 200
                if ok and data is not None:
                    if self._param_styles != [style]:
                        self._param_styles = [style]
                    time.sleep(self.pause)
                    return j
                last = f"{path}: {j.get('message') or j.get('status')}"
            time.sleep(2 * (attempt + 1))
        raise SSIError(f"Gọi {path} thất bại: {last}")

    def _paged(self, path: str, params: dict, page_size: int = 1000, max_pages: int = 10) -> list[dict]:
        rows: list[dict] = []
        for page in range(1, max_pages + 1):
            j = self._get(path, {**params, "pageIndex": page, "pageSize": page_size})
            data = j.get("data") or []
            if isinstance(data, dict):
                data = [data]
            rows.extend(data)
            total = j.get("totalRecord")
            if len(data) < page_size or (total is not None and len(rows) >= int(total)):
                break
        return rows

    # ------------------------------------------------------------------ endpoints
    def index_components(self, index_code: str) -> list[str]:
        j = self._get("IndexComponents", {"indexCode": index_code, "pageIndex": 1, "pageSize": 1000})
        data = j.get("data") or []
        if isinstance(data, dict):
            data = [data]
        syms = []
        for item in data:
            comps = item.get("IndexComponent") or item.get("indexComponent") or []
            for c in comps:
                s = c.get("StockSymbol") or c.get("stockSymbol")
                if s:
                    syms.append(s.strip().upper())
        return sorted(set(syms))

    def daily_stock_price(self, frm: dt.date, to: dt.date, market: str = "HOSE", symbol: str = "") -> list[dict]:
        params = {"fromDate": fmt_date(frm), "toDate": fmt_date(to), "market": market}
        if symbol:
            params["symbol"] = symbol
        out = []
        for r in self._paged("DailyStockPrice", params):
            d = parse_date(r.get("TradingDate"))
            sym = (r.get("Symbol") or "").strip().upper()
            close = to_float(r.get("ClosePrice"))
            if not d or not sym or not close:
                continue
            fb, fs = to_float(r.get("ForeignBuyValTotal")), to_float(r.get("ForeignSellValTotal"))
            net = to_float(r.get("NetBuySellVal"))
            fnet = (fb - fs) if fb is not None and fs is not None else net
            out.append({
                "date": d, "symbol": sym,
                "open": to_float(r.get("OpenPrice")) or close,
                "high": to_float(r.get("HighestPrice")) or close,
                "low": to_float(r.get("LowestPrice")) or close,
                "close": close,
                "volume": to_float(r.get("TotalMatchVol")) or 0.0,
                "value": to_float(r.get("TotalMatchVal")),
                "fnet": fnet,
            })
        return out

    def daily_bulk(self, symbols: list[str], frm: dt.date, to: dt.date, today: dt.date | None = None, **_) -> list[dict]:
        """Cả sàn HOSE trong 1 lần gọi (kèm khối ngoại) + VN-Index."""
        keep = set(symbols)
        rows = [r for r in self.daily_stock_price(frm, to, market="HOSE") if r["symbol"] in keep]
        if "VNINDEX" in keep:
            try:
                rows += self.daily_ohlc("VNINDEX", frm, to)
            except SSIError as e:
                log.warning("Không cập nhật được VNINDEX: %s", e)
        return rows

    def daily_ohlc(self, symbol: str, frm: dt.date, to: dt.date) -> list[dict]:
        out = []
        for r in self._paged("DailyOhlc", {"symbol": symbol, "fromDate": fmt_date(frm), "toDate": fmt_date(to), "ascending": True}):
            d = parse_date(r.get("TradingDate"))
            close = to_float(r.get("Close"))
            if not d or not close:
                continue
            out.append({
                "date": d, "symbol": symbol.upper(),
                "open": to_float(r.get("Open")) or close, "high": to_float(r.get("High")) or close,
                "low": to_float(r.get("Low")) or close, "close": close,
                "volume": to_float(r.get("Volume")) or 0.0, "value": to_float(r.get("Value")),
                "fnet": None,
            })
        return out

    def daily_ohlc_range(self, symbol: str, frm: dt.date, to: dt.date, chunk_days: int = 90) -> list[dict]:
        """Tải lịch sử theo từng đoạn để tránh giới hạn khoảng ngày mỗi lần gọi."""
        rows, cur = [], frm
        while cur <= to:
            end = min(cur + dt.timedelta(days=chunk_days - 1), to)
            rows.extend(self.daily_ohlc(symbol, cur, end))
            cur = end + dt.timedelta(days=1)
        return rows
