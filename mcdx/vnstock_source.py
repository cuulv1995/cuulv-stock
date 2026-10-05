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

GROUP = {"VN30": "VN30", "VNMIDCAP": "VNMidCap", "VNMID": "VNMidCap", "VNMidcap": "VNMidCap", "VN100": "VN100",
         "VNSMALLCAP": "VNSmallCap", "VNSML": "VNSmallCap", "VNSmallCap": "VNSmallCap"}
INDEXES = {"VNINDEX", "VN30", "VN100", "HNXINDEX", "UPCOMINDEX"}


class VnstockClient:
    universe_label = "vnstock (KBS)"

    def __init__(self, pause: float | None = None, retries: int = 3):
        os.environ.setdefault("VNSTOCK_DISABLE_AGENT_SETUP", "1")
        # Khách: 20 lượt/phút → nghỉ ~3,2 s giữa các lần gọi; có API key: 60 lượt/phút → ~1,1 s
        self.pause = pause if pause is not None else (1.1 if os.environ.get("VNSTOCK_API_KEY") else 3.2)
        self.retries = retries
        self._Quote = self._Listing = self._Trading = None
        self.intraday = False       # chạy giữa phiên (12:00)
        self.calib_path = None      # nơi lưu hệ số quy đổi bảng giá để dùng lại khi lịch sử chưa có phiên hôm nay

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

    # ------------------------------------------------------------------ bảng giá (nhiều mã / 1 lần gọi)
    def price_board(self, symbols: list[str]) -> dict[str, dict]:
        """Bảng giá phiên hiện tại: OHLC, khối lượng, khối ngoại. Đơn vị gốc của KBS (chưa quy đổi)."""
        out: dict[str, dict] = {}
        stocks = [s for s in symbols if s not in INDEXES]
        for i in range(0, len(stocks), 50):
            batch = stocks[i:i + 50]
            try:
                df = self._call(lambda: self._Trading().price_board(symbols_list=batch, get_all=True))
            except Exception as e:
                log.warning("Không lấy được bảng giá: %s", e)
                continue
            if df is None or len(df) == 0:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[-1] for c in df.columns]
            df.columns = [str(c).lower() for c in df.columns]
            if "symbol" not in df.columns:
                log.warning("Bảng giá thiếu cột symbol: %s", list(df.columns)[:20])
                continue
            for r in df.to_dict("records"):
                def g(*names):
                    for n in names:
                        v = r.get(n)
                        try:
                            if v is not None and v == v:
                                return float(v)
                        except (TypeError, ValueError):
                            pass
                    return None
                out[str(r["symbol"]).upper()] = {
                    "open": g("open_price", "open"), "high": g("high_price", "high"), "low": g("low_price", "low"),
                    "close": g("close_price", "match_price", "close"), "volume": g("volume_accumulated", "total_volume", "volume"),
                    "value": g("total_value"), "fb": g("foreign_buy_volume"), "fs": g("foreign_sell_volume")}
        return out

    @staticmethod
    def _pow10(ratio: float | None) -> float | None:
        if not ratio or ratio <= 0:
            return None
        import math
        e = round(math.log10(ratio))
        return 10.0 ** e if abs(math.log10(ratio) - e) < 0.05 else None

    def foreign_today(self, symbols: list[str]) -> dict[str, float]:
        """Khối ngoại mua − bán (cổ phiếu) trong phiên hiện tại."""
        return {s: (b["fb"] or 0) - (b["fs"] or 0) for s, b in self.price_board(symbols).items()
                if b["fb"] is not None or b["fs"] is not None}

    def daily_bulk(self, symbols: list[str], frm: dt.date, to: dt.date, today: dt.date | None = None,
                   last_dates: dict | None = None) -> list[dict]:
        """Cập nhật các phiên gần nhất.

        Đường nhanh: phiên hôm nay lấy từ bảng giá (≈ 7 lần gọi cho 300 mã).
        Mã bị thiếu phiên trước đó (lỡ lịch chạy) mới tải lịch sử từng mã.
        """
        last_dates = last_dates or {}
        rows: list[dict] = []
        idx = self.daily_ohlc("VNINDEX", frm, to) if "VNINDEX" in symbols else []
        rows.extend(idx)
        sessions = sorted({r["date"] for r in idx})
        stocks = [s for s in symbols if s not in INDEXES]

        board, pf, vf = {}, None, None
        if today and (today in sessions or self.intraday) and stocks:
            board = self.price_board(stocks)
            # Hiệu chỉnh đơn vị bảng giá theo lịch sử của 1 mã tham chiếu
            for ref in [s for s in stocks if s in board][:3]:
                h = [r for r in self.daily_ohlc(ref, today - dt.timedelta(days=7), today) if r["date"] == today]
                b = board[ref]
                if h and b["close"] and b["volume"]:
                    pf, vf = self._pow10(h[0]["close"] / b["close"]), self._pow10(h[0]["volume"] / b["volume"]) if h[0]["volume"] else None
                    if pf and vf:
                        break
            calib = {}
            if self.calib_path:
                try:
                    import json
                    calib = json.loads(open(self.calib_path, encoding="utf-8").read())
                except Exception:
                    calib = {}
            if pf and vf and self.calib_path:
                try:
                    import json
                    open(self.calib_path, "w", encoding="utf-8").write(json.dumps({"pf": pf, "vf": vf}))
                except Exception:
                    pass
            elif calib.get("pf") and calib.get("vf"):
                pf, vf = calib["pf"], calib["vf"]
                log.info("Dùng hệ số bảng giá đã lưu (giá %g, khối lượng %g)", pf, vf)
            if not (pf and vf):
                log.warning("Không hiệu chỉnh được bảng giá, chuyển sang tải từng mã.")
                board = {}
            else:
                log.info("Bảng giá: %d mã (hệ số giá %g, khối lượng %g)", len(board), pf, vf)

        if board and today in sessions:
            prev = sessions[-2] if len(sessions) >= 2 else None
        else:
            prev = sessions[-1] if sessions else None
            if board and prev == today:
                prev = sessions[-2] if len(sessions) >= 2 else None
        slow = []
        for s in stocks:
            b = board.get(s)
            gap = prev is not None and (last_dates.get(s) is None or last_dates[s] < prev)
            if b and b["close"] and not gap:
                c = b["close"] * pf
                rows.append({"date": today, "symbol": s,
                             "open": (b["open"] or b["close"]) * pf, "high": (b["high"] or b["close"]) * pf,
                             "low": (b["low"] or b["close"]) * pf, "close": c, "volume": (b["volume"] or 0) * vf,
                             "value": b["value"], "fnet": ((b["fb"] or 0) - (b["fs"] or 0)) * vf * c
                             if (b["fb"] is not None or b["fs"] is not None) else None})
            else:
                slow.append(s)
        if slow:
            log.info("Tải từng mã cho %d mã (thiếu phiên hoặc không có trong bảng giá)", len(slow))
        for i, s in enumerate(slow, 1):
            try:
                got = self.daily_ohlc(s, frm, to)
                b = board.get(s)
                if b and (b["fb"] is not None or b["fs"] is not None):
                    for r in got:
                        if r["date"] == today:
                            r["fnet"] = ((b["fb"] or 0) - (b["fs"] or 0)) * vf * r["close"]
                rows.extend(got)
            except Exception as e:
                log.warning("Bỏ qua %s: %s", s, e)
            if i % 25 == 0:
                log.info("Tải từng mã %d/%d", i, len(slow))
        return rows

    def sectors(self) -> dict[str, str]:
        """Ngành của từng mã theo phân loại KBS (tiếng Việt)."""
        try:
            df = self._call(lambda: self._Listing().symbols_by_industries(lang="vi"))
        except Exception as e:
            log.warning("Không lấy được ngành: %s", e)
            return {}
        if df is None or len(df) == 0 or "symbol" not in df.columns:
            return {}
        col = "industry_name" if "industry_name" in df.columns else df.columns[-1]
        return {str(r["symbol"]).upper(): str(r[col]) for r in df.to_dict("records") if r.get(col)}
