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

# Trường cần lấy: (khóa chuẩn, tiền tố item_id của KBS, từ khóa tên tiếng Việt). Tên được ưu tiên khi có.
FIELDS = {
    "income": [
        ("revenue", [], ["doanh thu thuan", "thu nhap lai thuan"]),
        ("gross_profit", ["gross_profit"], ["loi nhuan gop"]),
        ("operating_profit", ["operating_profit_before_provision", "operating_profit"],
         ["loi nhuan thuan tu hoat dong kinh doanh"]),
        ("pbt", ["profit_before_tax"], ["tong loi nhuan ke toan truoc thue", "tong loi nhuan truoc thue"]),
        ("net_profit", ["profit_after_tax_for_shareholders", "net_profit_atttributable", "net_profit_attributable"],
         ["loi nhuan sau thue cua co dong cua cong ty me", "loi nhuan sau thue cua co dong cua ngan hang me"]),
        ("net_profit_total", ["net_profit"], ["loi nhuan sau thue"]),
    ],
    "cash": [
        ("cfo", ["operating_cash_flow"], ["luu chuyen tien thuan tu hoat dong kinh doanh"]),
        ("capex", ["payment_for_fixed_assets", "purchase_of_fixed_assets"], ["tien chi de mua sam, xay dung tscd", "mua sam tai san co dinh"]),
    ],
    "ratio": [
        ("eps_ttm", ["trailing_eps"], ["thu nhap tren moi co phan cua 4 quy"]),
        ("bvps", ["book_value_per_share"], ["gia tri so sach cua co phieu"]),
        ("pe", ["pe_ratio"], []),
        ("pb", ["pb_ratio"], []),
        ("ps", ["ps_ratio"], []),
        ("div_yield", ["dividend_yield"], ["ty suat co tuc"]),
        ("beta", ["beta"], []),
        ("roe", ["roe_trailling", "roe_trailing"], ["roe binh quan 4 quy"]),
        ("roa", ["roa_trailling", "roa_trailing"], ["roa binh quan 4 quy"]),
        ("gross_margin", ["gross_margin"], ["ty suat loi nhuan gop bien"]),
        ("net_margin", ["net_margin"], ["ty suat sinh loi tren doanh thu thuan"]),
        ("ebit_margin", ["ebit_margin"], []),
        ("rev_growth", [], ["tang truong doanh thu thuan"]),
        ("np_growth", [], ["tang truong loi nhuan sau thue cua cd"]),
        ("pbt_growth", [], ["tang truong loi nhuan truoc thue"]),
        ("equity_growth", [], ["tang truong von chu so huu"]),
        ("current_ratio", ["short_term_ratio"], ["ty so thanh toan hien hanh"]),
        ("quick_ratio", ["quick_ratio"], ["ty so thanh toan nhanh"]),
        ("interest_cover", ["interest_coverage"], ["kha nang thanh toan lai vay"]),
        ("debt_assets", ["debt_to_assets"], ["ty so no vay tren tong tai san"]),
        ("liab_assets", ["liabilities_to_assets"], ["ty so no tren tong tai san"]),
        ("equity_assets", ["equity_to_assets", "equity_total_assets"], ["ty so von chu so huu tren tong tai san", "von chu so huu/tong tai san"]),
        ("dso", ["days_of_sales_outstanding"], []),
        ("dio", ["days_of_inventory_on_hand"], []),
        ("nim", ["net_interest_margin"], ["ty le thu nhap lai thuan"]),
        ("cir", ["cost_income_ratio"], []),
        ("ldr", ["outstanding_loans_customer_deposits"], []),
        ("loan_growth", [], ["tang truong du no cho vay"]),
        ("deposit_growth", [], ["tang truong huy dong von"]),
        ("npl", [], ["ty le no xau", "no xau/"]),
        ("llr", ["loan_loss_provision_ratio"], []),
    ],
}


def norm(s) -> str:
    s = str(s or "").replace("Đ", "D").replace("đ", "d").replace("Ð", "D")
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()
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
    """Trả {khóa: {kỳ: giá trị}}: khớp tên tiếng Việt trước (chính xác hơn khi item_id trùng), rồi tiền tố item_id."""
    out: dict = {}
    if df is None or len(df) == 0:
        return out
    cols = period_cols(df)
    if not cols:
        return out
    if "row_number" in df.columns:
        df = df.assign(_rn=pd.to_numeric(df["row_number"], errors="coerce")).sort_values("_rn")
    ids = df["item_id"].astype(str).str.lower().tolist() if "item_id" in df.columns else [""] * len(df)
    names = [norm(x) for x in (df["item"] if "item" in df.columns else df.iloc[:, 0])]
    used = set()

    def has_data(i):
        return any(_num(df.iloc[i][c]) is not None for c in cols)

    for key, id_opts, name_opts in fields:
        idx = None
        for opt in name_opts:
            idx = next((i for i, n in enumerate(names) if n.startswith(opt) and i not in used and has_data(i)), None)
            if idx is None:
                idx = next((i for i, n in enumerate(names) if opt in n and i not in used and has_data(i)), None)
            if idx is not None:
                break
        if idx is None:
            for opt in id_opts:
                idx = next((i for i, x in enumerate(ids) if x.startswith(opt) and i not in used and has_data(i)), None)
                if idx is not None:
                    break
        if idx is None:
            continue
        used.add(idx)
        row = df.iloc[idx]
        out[key] = {str(c): _num(row[c]) for c in cols}
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

    def _series(self, fin, key: str, period_type: int, limit: int, method: str):
        """Gọi trực tiếp hàm lấy nhiều kỳ của vnstock với số kỳ mong muốn (mỗi kỳ = 1 lần gọi KBS)."""
        from vnstock.explorer.kbs import financial as kf
        aliases = getattr(kf, "_FINANCIAL_REPORT_KEY_ALIASES")
        df = fin._fetch_series_data(report_type=key, period_type=period_type, report_key=aliases[key], limit=limit)
        if df is not None and len(df) and getattr(fin, "standardize_columns", False):
            df = fin._apply_schema_standardization(df, method)
        time.sleep(self.pause * limit)
        return df

    def _try(self, label, fn, errors):
        for attempt in range(2):
            try:
                return fn()
            except Exception as e:  # noqa: PERF203
                errors[label] = f"{type(e).__name__}: {e}"[:300]
                time.sleep(10 * (attempt + 1))
        return None

    def raw(self, symbol: str, errors: dict | None = None) -> dict:
        errors = {} if errors is None else errors
        fin = self.Finance(symbol, period="quarter")
        return {
            "income": self._try("income_q", lambda: self._series(fin, "KQKD", 2, 5, "income_statement"), errors),
            "income_y": self._try("income_y", lambda: self._series(fin, "KQKD", 1, 3, "income_statement"), errors),
            "cash": self._try("cash_y", lambda: self._series(fin, "LCTT", 1, 1, "cash_flow"), errors),
            "ratio": self._try("ratio", lambda: self._call(lambda: fin.ratio(period="quarter")), errors),
        }

    def fetch(self, symbol: str, raw: dict | None = None) -> dict | None:
        raw = raw or self.raw(symbol)
        if all(v is None or len(v) == 0 for v in raw.values()):
            return None
        rec = {"income": extract(raw.get("income"), FIELDS["income"]),
               "income_y": extract(raw.get("income_y"), FIELDS["income"]),
               "cash": extract(raw.get("cash"), FIELDS["cash"]),
               "ratio": extract(raw.get("ratio"), FIELDS["ratio"])}
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
        errors: dict = {}
        t0 = time.time()
        raw = src.raw(s, errors)
        out[s] = {"errors": errors, "seconds": round(time.time() - t0, 1)}
        for k, df in raw.items():
            if df is None:
                out[s][k] = None
                continue
            out[s][k] = {"columns": [str(c) for c in df.columns], "attrs": {a: str(v) for a, v in df.attrs.items()},
                         "rows": json.loads(df.to_json(orient="records", force_ascii=False))}
        out[s]["parsed"] = src.fetch(s, raw)
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
