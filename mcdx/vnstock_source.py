"""Nguồn dữ liệu tạm thời: thư viện vnstock (nguồn KBS), dùng khi chưa có key SSI.

Cùng giao diện với SSIClient để run.py dùng chung:
    authenticate(), index_components(code), daily_ohlc(sym, frm, to), daily_ohlc_range(...), daily_bulk(symbols, frm, to)

Khác biệt so với SSI:
- Giá cổ phiếu KBS trả về theo nghìn đồng → nhân 1000 để lưu theo đồng như SSI.
- Khối ngoại chỉ có trong bảng giá của phiên hiện tại → chỉ lưu được từ ngày bắt đầu chạy, không có lịch sử.
- Giới hạn lượt gọi: khách 20 lượt/phút, đăng ký miễn phí 60 lượt/phút (đặt VNSTOCK_API_KEY trong GitHub Secrets).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import time

import pandas as pd

log = logging.getLogger("vnstock")

GROUP = {"VN30": "VN30", "VNMIDCAP": "VNMidCap", "VNMID": "VNMidCap", "VNMidcap": "VNMidCap", "VN100": "VN100"}
INDEXES = {"VNINDEX", "VN30", "VN100", "HNXINDEX", "UPCOMINDEX"}


class VnstockClient:
    universe_label = "vnstock (KBS)"

    def __init__(self, pause: float | None = None, retries: int = 3):
        os.environ.setdefault("VNSTOCK_DISABLE_AGENT_SETUP", "1")
        # Khách: 20 lượt/phút → nghỉ ~3,2 s giữa các lần gọi; có API key: 60 lượt/phút → ~1,1 s
        self.pause = pause if pause is not None else (1.1 if os.environ.get("VNSTOCK_API_KEY") else 3.2)
        self.retries = retries
        self._Quote = self._Listing = self._Trading = None

    # ------------------------------------------------------------------ setup
    def authenticate(self):
        try:
            from vnstock.explorer.kbs.listing import Listing
            from vnstock.explorer.kbs.quote import Quote
            from vnstock.explorer.kbs.trading import Trading
        except ImportError as e:
            raise RuntimeError(
                "Chưa cài vnstock: pip install --extra-index-url https://vnstocks.com/api/simple vnstock vnai") from e
        self._Quote, self._Listing, self._Trading = Quote, Listing, Trading
        key = os.environ.get("VNSTOCK_API_KEY")
        if key:
            try:
                import vnstock
                if hasattr(vnstock, "register_user"):
                    vnstock.register_user(api_key=key)
            except Exception as e:  # không bắt buộc
                log.info("Không đăng ký được VNSTOCK_API_KEY (%s), chạy ở chế độ khách.", e)
        log.info("Dùng nguồn vnstock (KBS), nghỉ %.1f s giữa các lần gọi", self.pause)

    def _call(self, fn, *a, **k):
        last = None
        for attempt in range(self.retries):
            try:
                out = fn(*a, **k)
                time.sleep(self.pause)
                return out
            except SystemExit as e:  # vnai có thể dừng khi vượt hạn mức
                last = e
            except Exception as e:
                last = e
            wait = 20 * (attempt + 1)
            log.warning("vnstock lỗi (%s), thử lại sau %ds", last, wait)
            time.sleep(wait)
        raise RuntimeError(f"vnstock thất bại: {last}")

    # ------------------------------------------------------------------ endpoints
    def index_components(self, index_code: str) -> list[str]:
        group = GROUP.get(index_code, index_code)
        res = self._call(lambda: self._Listing().symbols_by_group(group=group))
        if isinstance(res, pd.DataFrame):
            col = next((c for c in res.columns if str(c).lower() in ("symbol", "ticker", "code")), res.columns[0])
            res = res[col]
        return sorted({str(s).strip().upper() for s in list(res) if str(s).strip()})

    def daily_ohlc(self, symbol: str, frm: dt.date, to: dt.date) -> list[dict]:
        sym = symbol.upper()
        df = self._call(lambda: self._Quote(sym).history(start=frm.isoformat(), end=to.isoformat(), interval="1D"))
        if df is None or len(df) == 0:
            return []
        scale = 1 if sym in INDEXES else 1000  # KBS trả giá cổ phiếu theo nghìn đồng
        out = []
        for r in df.itertuples(index=False):
            d = pd.Timestamp(r.time).date()
            if d < frm or d > to:
                continue
            close = float(r.close) * scale
            if not close:
                continue
            out.append({"date": d, "symbol": sym,
                        "open": float(r.open) * scale, "high": float(r.high) * scale, "low": float(r.low) * scale,
                        "close": close, "volume": float(r.volume or 0), "value": None, "fnet": None})
        return out

    def daily_ohlc_range(self, symbol: str, frm: dt.date, to: dt.date, chunk_days: int = 0) -> list[dict]:
        return self.daily_ohlc(symbol, frm, to)

    def foreign_today(self, symbols: list[str]) -> dict[str, float]:
        """Khối ngoại mua − bán (cổ phiếu) trong phiên hiện tại, từ bảng giá."""
        out: dict[str, float] = {}
        stocks = [s for s in symbols if s not in INDEXES]
        for i in range(0, len(stocks), 50):
            batch = stocks[i:i + 50]
            try:
                df = self._call(lambda: self._Trading().price_board(symbols_list=batch))
            except Exception as e:
                log.warning("Không lấy được bảng giá (khối ngoại): %s", e)
                continue
            if df is None or len(df) == 0:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[-1] for c in df.columns]
            cols = {str(c).lower(): c for c in df.columns}
            sc, fb, fs = cols.get("symbol"), cols.get("foreign_buy_volume"), cols.get("foreign_sell_volume")
            if not (sc and fb and fs):
                log.warning("Bảng giá không có cột khối ngoại: %s", list(df.columns)[:20])
                continue
            for r in df[[sc, fb, fs]].itertuples(index=False):
                try:
                    out[str(r[0]).upper()] = float(r[1] or 0) - float(r[2] or 0)
                except (TypeError, ValueError):
                    pass
        return out

    def daily_bulk(self, symbols: list[str], frm: dt.date, to: dt.date, today: dt.date | None = None) -> list[dict]:
        """Cập nhật vài phiên gần nhất cho từng mã + khối ngoại phiên hôm nay."""
        rows: list[dict] = []
        for i, s in enumerate(symbols, 1):
            try:
                rows.extend(self.daily_ohlc(s, frm, to))
            except Exception as e:
                log.warning("Bỏ qua %s: %s", s, e)
            if i % 20 == 0:
                log.info("Cập nhật giá %d/%d mã", i, len(symbols))
        if today:
            fvol = self.foreign_today(symbols)
            for r in rows:
                if r["date"] == today and r["symbol"] in fvol:
                    r["fnet"] = fvol[r["symbol"]] * r["close"]  # quy ra đồng
            log.info("Khối ngoại hôm nay: %d mã", len(fvol))
        return rows
