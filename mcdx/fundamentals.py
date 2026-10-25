"""Báo cáo tài chính theo quý (nguồn vnstock / KBS), lưu đệm trong data/fundamentals.json.

Mỗi mã cần ~4 lần gọi (KQKD, CĐKT, LCTT, chỉ số) nên làm mới theo lô mỗi lần chạy:
mã chưa có hoặc dữ liệu cũ hơn REFRESH_DAYS được ưu tiên, mã đang có tín hiệu được ưu tiên trước nhất.

    python -m mcdx.fundamentals --probe FPT,VCB,HAH   # ghi cấu trúc thô ra data/debug/fund_probe.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os
import re
import time
import unicodedata
from pathlib import Path

import pandas as pd

from . import store

log = logging.getLogger("fund")
CACHE = store.DATA / "fundamentals.json"
REFRESH_DAYS = 30
QUARTERS = 8

# Trường cần lấy: (khóa chuẩn, item_id có thể gặp, từ khóa tên tiếng Việt)
FIELDS = {
    "income": [
        ("revenue", ["revenue", "net_revenue", "operating_income"],
         ["doanh thu thuan", "tong thu nhap hoat dong", "thu nhap lai thuan", "doanh thu hoat dong"]),
        ("gross_profit", ["gross_profit"], ["loi nhuan gop"]),
        ("operating_profit", ["operating_profit"], ["loi nhuan thuan tu hoat dong kinh doanh", "loi nhuan tu hoat dong kinh doanh"]),
        ("pbt", ["profit_before_tax"], ["tong loi nhuan ke toan truoc thue", "loi nhuan truoc thue"]),
        ("net_profit", ["net_profit", "net_profit_after_tax"],
         ["loi nhuan sau thue cua co dong cua cong ty me", "loi nhuan sau thue cua chu so huu", "loi nhuan sau thue thu nhap doanh nghiep", "loi nhuan sau thue"]),
        ("eps", ["eps"], ["lai co ban tren co phieu", "eps"]),
    ],
    "balance": [
        ("total_assets", ["total_assets", "assets"], ["tong cong tai san", "tong tai san"]),
        ("equity", ["equity", "owners_equity"], ["von chu so huu"]),
        ("liabilities", ["total_liabilities", "liabilities"], ["no phai tra"]),
        ("current_assets", ["current_assets", "short_term_assets"], ["tai san ngan han"]),
        ("current_liabilities", ["current_liabilities", "short_term_liabilities"], ["no ngan han"]),
        ("cash", [], ["tien va cac khoan tuong duong tien"]),
        ("short_debt", [], ["vay va no thue tai chinh ngan han", "vay ngan han"]),
        ("long_debt", [], ["vay va no thue tai chinh dai han", "vay dai han"]),
        ("inventory", [], ["hang ton kho"]),
        ("charter_capital", ["charter_capital", "share_capital"], ["von gop cua chu so huu", "von dieu le"]),
    ],
    "cash": [
        ("cfo", ["operating_cash_flow", "net_cash_flows_from_operating_activities"],
         ["luu chuyen tien thuan tu hoat dong kinh doanh", "luu chuyen tien te rong tu cac hoat dong san xuat kinh doanh",
          "luu chuyen tien thuan tu cac hoat dong kinh doanh"]),
        ("capex", [], ["tien chi de mua sam, xay dung tscd", "mua sam, xay dung tai san co dinh", "tien chi mua sam"]),
    ],
    "ratio": [
        ("pe", ["pe_ratio", "p_e"], ["p/e"]),
        ("pb", ["pb_ratio", "p_b"], ["p/b"]),
        ("roe", ["roe"], ["roe", "ty suat loi nhuan tren von chu so huu"]),
        ("roa", ["roa"], ["roa", "ty suat loi nhuan tren tong tai san"]),
        ("gross_margin", ["gross_margin", "gross_profit_margin"], ["bien loi nhuan gop", "ty suat loi nhuan gop"]),
        ("net_margin", ["net_margin", "net_profit_margin"], ["bien loi nhuan rong", "ty suat loi nhuan rong", "bien loi nhuan sau thue"]),
        ("de", ["debt_to_equity", "debt_equity"], ["no/von chu so huu", "no phai tra/von chu so huu"]),
        ("current_ratio", ["current_ratio"], ["thanh toan hien hanh", "kha nang thanh toan ngan han"]),
        ("bvps", ["book_value_per_share"], ["gia tri so sach", "bvps"]),
        ("eps_ttm", ["earnings_per_share"], ["eps 4 quy", "thu nhap tren moi co phan"]),
        ("npl", [], ["no xau", "ty le no xau"]),
        ("nim", [], ["nim", "bien lai thuan"]),
        ("casa", [], ["casa", "tien gui khong ky han"]),
    ],
}


def norm(s) -> str:
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = s.replace("đ", "d")
    return re.sub(r"\s+", " ", re.sub(r"^[\divxlc]+[\.\)]\s*|^[-–\s]+", "", s)).strip()


def _num(v):
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def period_cols(df: pd.DataFrame) -> list[str]:
    """Các cột kỳ báo cáo (VD '2026-Q2'), sắp từ cũ đến mới."""
    cols = [c for c in df.columns if re.match(r"^\d{4}(-Q\d)?(_\d+)?$", str(c))]
    def key(c):
        m = re.match(r"^(\d{4})(?:-Q(\d))?", str(c))
        return (int(m.group(1)), int(m.group(2) or 0))
    return sorted(cols, key=key)


def extract(df: pd.DataFrame | None, fields) -> dict:
    """Trả {khóa: {kỳ: giá trị}} bằng cách khớp item_id trước, sau đó khớp tên tiếng Việt."""
    out: dict = {}
    if df is None or len(df) == 0:
        return out
    cols = period_cols(df)
    if not cols:
        return out
    ids = df["item_id"].astype(str).str.lower().tolist() if "item_id" in df.columns else [""] * len(df)
    names = [norm(x) for x in (df["item"] if "item" in df.columns else df.iloc[:, 0])]
    used = set()
    for key, id_opts, name_opts in fields:
        idx = next((i for i, x in enumerate(ids) if x in id_opts and i not in used), None)
        if idx is None:
            for opt in name_opts:  # khớp tên bắt đầu bằng từ khóa trước, rồi chứa từ khóa
                idx = next((i for i, n in enumerate(names) if n.startswith(opt) and i not in used), None)
                if idx is None:
                    idx = next((i for i, n in enumerate(names) if opt in n and i not in used), None)
                if idx is not None:
                    break
        if idx is None:
            continue
        used.add(idx)
        row = df.iloc[idx]
        vals = {str(c): _num(row[c]) for c in cols}
        if any(v is not None for v in vals.values()):
            out[key] = vals
    return out


class FundSource:
    """Lấy báo cáo tài chính qua vnstock (KBS)."""

    def __init__(self, pause: float | None = None):
        os.environ.setdefault("VNSTOCK_DISABLE_AGENT_SETUP", "1")
        from vnstock.explorer.kbs.financial import Finance  # noqa: F401
        self.Finance = Finance
        self.pause = pause if pause is not None else (1.1 if os.environ.get("VNSTOCK_API_KEY") else 3.2)

    def _call(self, fn):
        for attempt in range(3):
            try:
                out = fn()
                time.sleep(self.pause)
                return out
            except SystemExit as e:
                err = e
            except Exception as e:
                err = e
            time.sleep(15 * (attempt + 1))
        log.warning("Báo cáo tài chính lỗi: %s", err)
        return None

    def _statement(self, fin, method: str, report_type: str):
        """Lấy QUARTERS kỳ nếu được (hàm nội bộ có tham số limit), không thì mặc định 4 kỳ."""
        try:
            from vnstock.explorer.kbs import financial as kf
            aliases = getattr(kf, "_FINANCIAL_REPORT_KEY_ALIASES", {})
            key = {"income_statement": "KQKD", "balance_sheet": "CDKT", "cash_flow": "LCTT"}[method]
            if hasattr(fin, "_fetch_series_data") and key in aliases:
                df = self._call(lambda: fin._fetch_series_data(report_type=key, period_type=2, report_key=aliases[key],
                                                               limit=QUARTERS))
                if df is not None and len(df):
                    if getattr(fin, "standardize_columns", False):
                        df = fin._apply_schema_standardization(df, method)
                    return df
        except Exception as e:
            log.debug("Không dùng được hàm nội bộ (%s), lấy mặc định.", e)
        return self._call(lambda: getattr(fin, method)(period="quarter"))

    def raw(self, symbol: str) -> dict:
        fin = self.Finance(symbol, period="quarter")
        try:
            fin.limit = QUARTERS
        except Exception:
            pass
        return {
            "income": self._statement(fin, "income_statement", "income_statement"),
            "balance": self._statement(fin, "balance_sheet", "balance_sheet"),
            "cash": self._statement(fin, "cash_flow", "cash_flow"),
            "ratio": self._call(lambda: fin.ratio(period="quarter")),
        }

    def fetch(self, symbol: str) -> dict | None:
        raw = self.raw(symbol)
        if all(v is None or len(v) == 0 for v in raw.values()):
            return None
        rec = {k: extract(raw[k], FIELDS[k]) for k in FIELDS}
        units = {}
        for k, df in raw.items():
            if df is not None and hasattr(df, "attrs"):
                units[k] = str(df.attrs.get("unit", "")) or None
        rec["updated"] = dt.date.today().isoformat()
        rec["units"] = units
        return rec


def load_cache() -> dict:
    return store.read_json(CACHE, {})


def refresh(symbols: list[str], priority: list[str], budget: int, today: dt.date) -> dict:
    """Làm mới tối đa `budget` mã. Trả về cache đầy đủ."""
    cache = load_cache()
    if budget <= 0:
        return cache
    try:
        src = FundSource()
    except Exception as e:
        log.warning("Không dùng được nguồn báo cáo tài chính: %s", e)
        return cache

    def age(s):
        u = cache.get(s, {}).get("updated")
        return (today - dt.date.fromisoformat(u)).days if u else 10_000
    due = [s for s in symbols if age(s) >= REFRESH_DAYS]
    pri = set(priority)
    due.sort(key=lambda s: (s not in pri, -age(s)))
    todo = due[:budget]
    if todo:
        log.info("Báo cáo tài chính: làm mới %d/%d mã đến hạn", len(todo), len(due))
    for i, s in enumerate(todo, 1):
        rec = src.fetch(s)
        if rec:
            cache[s] = rec
        else:
            cache.setdefault(s, {})["updated"] = today.isoformat()  # tránh thử lại liên tục mã không có dữ liệu
        if i % 10 == 0:
            store.write_json(CACHE, cache)
            log.info("Báo cáo tài chính %d/%d", i, len(todo))
    store.write_json(CACHE, cache)
    return cache


def probe(symbols: list[str]):
    src = FundSource()
    out = {}
    for s in symbols:
        raw = src.raw(s)
        out[s] = {}
        for k, df in raw.items():
            if df is None:
                out[s][k] = None
                continue
            out[s][k] = {"columns": [str(c) for c in df.columns], "attrs": {a: str(v) for a, v in df.attrs.items()},
                         "rows": json.loads(df.head(80).to_json(orient="records", force_ascii=False))}
        out[s]["parsed"] = src.fetch(s)
    path = store.DATA / "debug" / "fund_probe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log.info("Đã ghi %s", path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", help="Danh sách mã, cách nhau bởi dấu phẩy")
    a = ap.parse_args()
    if a.probe:
        probe([s.strip().upper() for s in a.probe.split(",") if s.strip()])
