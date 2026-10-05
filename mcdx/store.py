"""Kho dữ liệu dạng CSV trong thư mục data/ (được commit lại vào repo mỗi ngày)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = ROOT / "config"
PRICES = DATA / "prices.csv"
UNIVERSE = DATA / "universe.json"
STATUS = DATA / "status.json"
SIGNALS = DATA / "signals_history.csv"
COLS = ["date", "symbol", "open", "high", "low", "close", "volume", "value", "fnet"]
KEEP_DAYS = 420


def load_prices() -> pd.DataFrame:
    if not PRICES.exists():
        return pd.DataFrame(columns=COLS)
    df = pd.read_csv(PRICES, parse_dates=["date"])
    df["date"] = df["date"].dt.date
    return df


def upsert_prices(df: pd.DataFrame, rows: list[dict], today: dt.date) -> pd.DataFrame:
    """Ghi đè theo (date, symbol). fnet mới chỉ ghi đè khi có giá trị."""
    if not rows:
        return df
    new = pd.DataFrame(rows, columns=COLS)
    if len(df):
        old_f = df.set_index(["date", "symbol"])["fnet"]
        key = list(zip(new["date"], new["symbol"]))
        prev = old_f.reindex(key).to_numpy()
        new["fnet"] = new["fnet"].where(new["fnet"].notna(), prev)
    out = pd.concat([df, new]).drop_duplicates(["date", "symbol"], keep="last")
    out = out[out["date"] >= today - dt.timedelta(days=KEEP_DAYS)]
    return out.sort_values(["symbol", "date"]).reset_index(drop=True)


def save_prices(df: pd.DataFrame):
    DATA.mkdir(exist_ok=True)
    out = df.copy()
    for c in ["open", "high", "low", "close"]:
        out[c] = out[c].round(2)
    out.to_csv(PRICES, index=False)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, obj):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def load_holdings() -> list[str]:
    p = CONFIG / "holdings.txt"
    if not p.exists():
        return []
    syms = []
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.split("#")[0].strip().upper()
        if s:
            syms.append(s)
    return syms


def load_sectors() -> dict[str, str]:
    """Ngành do người dùng đặt (config/sectors.json) ưu tiên hơn ngành tự động từ nguồn dữ liệu."""
    auto = read_json(DATA / "sectors_auto.json", {})
    return {**auto, **read_json(CONFIG / "sectors.json", {})}
