"""Báo cáo tài chính theo quý (nguồn vnstock / KBS), lưu đệm trong data/fundamentals.json.

Mỗi mã cần vài lần gọi nên làm mới theo lô mỗi lần chạy. Mã đến hạn khi: chưa có dữ liệu, dữ liệu cũ hơn
REFRESH_DAYS, hoặc đang mùa báo cáo (trong SEASON_DAYS ngày sau khi quý kết thúc) mà mã chưa có quý vừa kết thúc
và lần kiểm tra trước đã qua RECHECK_DAYS ngày. Thứ tự: chưa có dữ liệu → chưa có quý mới → mã đang có tín hiệu.

    python -m mcdx.fundamentals --probe FPT,VCB,HAH          # ghi cấu trúc thô ra data/debug/fund_probe.json
    python -m mcdx.fundamentals --refresh --shard 0/10       # làm mới phần 0 trong 10 phần, ghi data/fund_parts/
    python -m mcdx.fundamentals --merge data/fund_parts      # gộp các phần vào data/fundamentals.json
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
RECHECK_DAYS = 2    # mùa báo cáo: kiểm tra lại mã chưa có quý mới sau mỗi 2 ngày
SEASON_DAYS = 60    # báo cáo quý hợp nhất/công ty mẹ có thể nộp đến ~45 ngày sau khi quý kết thúc
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
        ("nii_growth", [], ["tang truong thu nhap lai thuan"]),
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
    cols = [c for c in df.columns if re.match(r"^\d{4}(-Q\d|-N\w*)?(_\d+)?$", str(c))]
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

    def _ratio_latest(self, fin):
        """Bảng chỉ số của kỳ mới nhất, lấy 1 kỳ/lần (lấy 4 kỳ một lúc thì KBS gán nhầm kỳ)."""
        data = fin._fetch_financial_data(report_type="CSTC", period_type=2, page=1, page_size=1)
        time.sleep(self.pause)
        if not data or not data.get("Head"):
            return None
        content = data.get("Content", {}) or {}
        rows = [r for k, v in content.items() if "Nhóm chỉ số" in k for r in (v or [])]
        if not rows:
            return None
        data["Content"]["Financial Ratios Combined"] = rows
        df = fin._parse_financial_response(data, "Financial Ratios Combined")
        if df is not None and len(df) and getattr(fin, "standardize_columns", False):
            df = fin._apply_schema_standardization(df, "financial_ratios")
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
        # Dữ liệu năm và lưu chuyển tiền tệ của KBS bị lẫn số giữa các kỳ khi lấy theo trang → không dùng.
        return {
            "income": self._try("income_q", lambda: self._series(fin, "KQKD", 2, 4, "income_statement"), errors),
            "ratio": self._try("ratio", lambda: self._ratio_latest(fin), errors),
        }

    def fetch(self, symbol: str, raw: dict | None = None) -> dict | None:
        raw = raw or self.raw(symbol)
        if all(v is None or len(v) == 0 for v in raw.values()):
            return None
        rec = {"income": extract(raw.get("income"), FIELDS["income"]),
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


def expected_quarter(today: dt.date) -> tuple[str, dt.date]:
    """Quý gần nhất đã kết thúc (nhãn kiểu KBS '2026-Q3') và ngày kết thúc quý."""
    q = (today.month - 1) // 3
    y = today.year if q else today.year - 1
    q = q or 4
    end = dt.date(y, 3 * q, 31 if q in (1, 4) else 30)
    return f"{y}-Q{q}", end


def latest_period(rec: dict | None) -> str | None:
    ps = [p for part in ("income", "ratio") for v in ((rec or {}).get(part) or {}).values() if isinstance(v, dict)
          for p, x in v.items() if x is not None and "_" not in p]
    return max(ps) if ps else None


def due_reason(rec: dict | None, today: dt.date) -> str | None:
    """Lý do mã cần làm mới: 'missing', 'new_quarter', 'old' hoặc None."""
    u = (rec or {}).get("updated")
    if not u or latest_period(rec) is None:
        return "missing" if not u or (today - dt.date.fromisoformat(u)).days >= RECHECK_DAYS else None
    age = (today - dt.date.fromisoformat(u)).days
    exp, end = expected_quarter(today)
    if (today - end).days <= SEASON_DAYS and latest_period(rec) < exp and age >= RECHECK_DAYS:
        return "new_quarter"
    return "old" if age >= REFRESH_DAYS else None


def due_list(symbols: list[str], cache: dict, priority: list[str], today: dt.date) -> list[str]:
    rank = {"missing": 0, "new_quarter": 1, "old": 2}
    pri = set(priority)
    due = [(rank[r], s not in pri, i, s) for i, s in enumerate(dict.fromkeys(symbols))
           if (r := due_reason(cache.get(s), today))]
    return [s for *_, s in sorted(due)]


def refresh(symbols: list[str], priority: list[str], budget: int, today: dt.date, seconds: float | None = None,
            shard: tuple[int, int] | None = None, part_path: Path | None = None) -> dict:
    """Làm mới tối đa `budget` mã và dừng khi hết `seconds` giây. Trả về cache đầy đủ.
    shard=(i, n): chỉ làm phần thứ i trong n phần của danh sách đến hạn (chạy song song nhiều máy);
    part_path: ghi riêng các mã vừa làm mới ra file này để gộp sau."""
    t0 = time.monotonic()
    cache = load_cache()
    if budget <= 0:
        return cache
    try:
        src = FundSource()
    except Exception as e:
        log.warning("Không dùng được nguồn báo cáo tài chính: %s", e)
        return cache
    due = due_list(symbols, cache, priority, today)
    if shard:
        due = due[shard[0]::shard[1]]
    todo = due[:budget]
    if todo:
        why = {}
        for x in todo:
            r = due_reason(cache.get(x), today)
            why[r] = why.get(r, 0) + 1
        log.info("Báo cáo tài chính: làm mới %d/%d mã đến hạn %s", len(todo), len(due), why)
    changed: dict = {}

    def save():
        store.write_json(CACHE, cache)
        if part_path:
            part_path.parent.mkdir(parents=True, exist_ok=True)
            store.write_json(part_path, changed)
    for i, s in enumerate(todo, 1):
        if seconds and time.monotonic() - t0 > seconds:
            log.info("Báo cáo tài chính: hết thời gian, đã làm mới %d mã", i - 1)
            break
        before = latest_period(cache.get(s))
        rec = src.fetch(s)
        if rec:
            cache[s] = rec
        else:
            cache.setdefault(s, {})["updated"] = today.isoformat()  # tránh thử lại liên tục mã không có dữ liệu
        changed[s] = cache[s]
        after = latest_period(cache.get(s))
        if after and after != before:
            log.info("%s: %s → %s", s, before or "chưa có", after)
        if i % 10 == 0:
            save()
            log.info("Báo cáo tài chính %d/%d", i, len(todo))
    save()
    return cache


def merge(parts_dir: Path) -> dict:
    """Gộp các file phần (mã → bản ghi) vào cache; bản ghi có dữ liệu không bị ghi đè bởi bản ghi rỗng."""
    cache = load_cache()
    n = 0
    for f in sorted(Path(parts_dir).rglob("*.json")):
        for s, rec in json.loads(f.read_text(encoding="utf-8")).items():
            if latest_period(rec) is None and latest_period(cache.get(s)) is not None:
                cache[s]["updated"] = rec.get("updated", cache[s].get("updated"))
            else:
                cache[s] = rec
            n += 1
    store.write_json(CACHE, cache)
    log.info("Đã gộp %d mã vào %s (tổng %d mã)", n, CACHE, len(cache))
    return cache


def summary(today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    cache = load_cache()
    exp, _ = expected_quarter(today)
    per: dict = {}
    for rec in cache.values():
        p = latest_period(rec) or "không có"
        per[p] = per.get(p, 0) + 1
    return {"total": len(cache), "expected_quarter": exp, "with_expected": sum(v for k, v in per.items() if k >= exp and k != "không có"),
            "periods": dict(sorted(per.items()))}


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
    ap.add_argument("--refresh", action="store_true", help="Làm mới các mã đến hạn trong rổ VN30/MID/SMALL")
    ap.add_argument("--shard", default="0/1", help="i/n: làm phần thứ i trong n phần")
    ap.add_argument("--minutes", type=float, default=50)
    ap.add_argument("--merge", help="Thư mục chứa các file phần để gộp")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--recheck", type=int, help="Số ngày tối thiểu giữa hai lần kiểm tra quý mới (mặc định RECHECK_DAYS)")
    a = ap.parse_args()
    if a.recheck is not None:
        RECHECK_DAYS = a.recheck
    if a.probe:
        probe([s.strip().upper() for s in a.probe.split(",") if s.strip()])
    if a.refresh:
        i, n = (int(x) for x in a.shard.split("/"))
        uni = store.read_json(store.UNIVERSE, {})
        syms = [s for b in ("VN30", "MID", "SMALL") for s in uni.get(b, [])]
        refresh(syms, store.read_json(store.DATA / "signal_syms.json", []), 10_000, dt.date.today(),
                seconds=a.minutes * 60, shard=(i, n), part_path=store.DATA / "fund_parts" / f"part_{i:02d}.json")
    if a.merge:
        merge(Path(a.merge))
    if a.summary or a.merge:
        print(json.dumps(summary(), ensure_ascii=False))
